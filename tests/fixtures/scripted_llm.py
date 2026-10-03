"""A stand-in for the model that replays a fixed script of tool calls and a final message.

What: `ScriptedLLM` implements the ChatModel protocol. Each `complete` call returns the next
step of its script: either a set of tool calls or a final text message.
Why: integration tests must run whole episodes without calling any API (SPEC 13).
How: a test builds `ScriptedLLM([call("write_note", name="a", text="b"), final("DONE ok")])`
and passes it to the agent loop. Every conversation it received is kept in `.seen` so tests
can check what the model would have been shown (for example tool results).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from pruefstand.agent.llm import LLMError, LLMReply, ToolCall


@dataclass
class Step:
    """One scripted model turn: tool calls, or a final message, or an error."""

    tool_calls: list[tuple[str, str]] | None = None  # (name, raw JSON arguments)
    text: str = ""
    error: Exception | None = None
    delay_s: float = 0.0  # simulated time spent in the call
    throttle_s: float = 0.0  # how much of that delay is reported as quota waiting
    parse_retries: int = 0  # provider parse failures the real client would have retried
    reasoning: str = ""  # reasoning text the provider returned next to the reply
    tokens_out: int = 5  # output tokens the reply reports


def call(tool: str, /, **arguments) -> Step:
    """A turn with one tool call."""
    return Step(tool_calls=[(tool, json.dumps(arguments))])


def raw_call(name: str, raw_arguments: str) -> Step:
    """A turn with one tool call whose arguments are given as raw text (may be invalid JSON)."""
    return Step(tool_calls=[(name, raw_arguments)])


def final(text: str) -> Step:
    """A final message, which ends the episode."""
    return Step(text=text)


def empty(reasoning: str = "Let's list the directory.", tokens_out: int = 10) -> Step:
    """An empty reply: no text, no tool call, finish_reason "stop" (re-sampled by the loop).

    With few output tokens beyond the reasoning it reads as "stopped after reasoning"; with
    many (for example tokens_out=60) as "dropped call".
    """
    return Step(text="", reasoning=reasoning, tokens_out=tokens_out)


def fail(error: Exception | None = None) -> Step:
    """A turn where the model call itself fails."""
    return Step(error=error or LLMError("scripted failure"))


class ScriptedLLM:
    """ChatModel that replays a script. Running past the end returns "FAILED out of script"."""

    def __init__(
        self, steps: list[Step], model_version: str = "scripted-1", provider: str = "ScriptedCo"
    ):
        self.steps = list(steps)
        self.model_version = model_version
        self.provider = provider  # reported like OpenRouter's upstream provider name
        self.position = 0
        self.seen: list[list[dict]] = []  # a copy of the messages at every call

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        self.seen.append([dict(m) for m in messages])
        if self.position < len(self.steps):
            step = self.steps[self.position]
        else:
            step = final("FAILED out of script")
        self.position += 1
        if step.delay_s:
            await asyncio.sleep(step.delay_s)
        if step.error is not None:
            raise step.error
        calls = [
            ToolCall(id=f"call_{self.position}_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(step.tool_calls or [])
        ]
        message: dict = {"role": "assistant", "content": step.text}
        if calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in calls
            ]
        return LLMReply(
            content=step.text,
            tool_calls=calls,
            message=message,
            tokens_in=10,
            tokens_out=step.tokens_out,
            tokens_cached=4,  # as if part of the prompt came from the provider's cache
            model_version=self.model_version,
            provider=self.provider,
            throttle_s=step.throttle_s,
            parse_retries=step.parse_retries,
            attempts=1 + step.parse_retries,
            # Like the real API: "tool_calls" when the reply calls tools, else "stop".
            finish_reason="tool_calls" if calls else "stop",
            extra={"reasoning": step.reasoning} if step.reasoning else {},
        )
