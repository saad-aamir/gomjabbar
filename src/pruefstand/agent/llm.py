"""Model client: one chat completion with tools, through LiteLLM, with retries and accounting.

What: `LiteLLMChat.complete` sends the conversation and tool list to the model and returns an
`LLMReply` with the text, tool calls, token counts, cost and the provider's model version.
Why: every model call in the bench goes through here (SPEC 5.3), so retries, quota throttling,
the explicit API key and cost accounting live in one place. The agent loop only sees the
`ChatModel` protocol, which tests satisfy with a scripted stand-in.
How: before each HTTP attempt the client asks the quota gate (runner/quota.py) for permission;
LiteLLM's own retries are switched off so every real request is counted exactly once.
Transient errors are retried with backoff; a daily-quota 429 raises QuotaExhausted; anything
else raises LLMError, which ends the episode with stop_reason "llm_error".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from pruefstand.config import ModelConfig

# How many HTTP attempts one completion may use before giving up.
MAX_ATTEMPTS = 6
# Longest single wait between attempts, in seconds.
MAX_BACKOFF_S = 65.0


class LLMError(Exception):
    """A model call failed for good. The episode stops with stop_reason "llm_error"."""

    # HTTP requests the failed call used; they count against the quota like any other.
    attempts: int = 0


class QuotaExhausted(LLMError):
    """The provider's daily quota for this model is used up. The run should pause, not fail."""


@dataclass
class ToolCall:
    """One tool call requested by the model."""

    id: str  # provider's id, echoed back in the tool result message
    name: str  # tool name
    arguments: str  # raw JSON text of the arguments, exactly as the model wrote it


@dataclass
class LLMReply:
    """What one completion returned."""

    content: str  # assistant text ("" if none)
    tool_calls: list[ToolCall]
    message: dict  # the assistant message in OpenAI format, appended to the history
    tokens_in: int = 0
    tokens_out: int = 0
    cost_eur: float = 0.0
    model_version: str = ""
    latency_ms: float = 0.0
    attempts: int = 1  # HTTP requests this completion used (retries included)
    throttle_s: float = 0.0  # time spent waiting on our quota gate and 429 backoffs
    finish_reason: str = ""  # as reported by the provider ("stop", "tool_calls", "length", ...)
    extra: dict = field(default_factory=dict)  # e.g. reasoning text, kept for the trace


class ChatModel(Protocol):
    """What the agent loop needs from a model. LiteLLMChat and the scripted LLM implement it."""

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply: ...


class RequestGate(Protocol):
    """Quota throttle consulted before every HTTP request (implemented in runner/quota.py)."""

    async def acquire(self, estimated_tokens: int) -> None: ...

    def record(self, tokens: int) -> None: ...

    def mark_day_exhausted(self) -> None: ...


# ---- error classification ------------------------------------------------------------------

# Groq names the limit in the message, e.g. "on requests per day (RPD)" or "tokens per day (TPD)".
_DAILY_RE = re.compile(r"per day|\(RPD\)|\(TPD\)", re.IGNORECASE)
# Groq suggests a wait, e.g. "Please try again in 7.66s" or "in 1m2.5s".
_RETRY_IN_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


@dataclass
class ErrorVerdict:
    """What to do about one failed request."""

    action: str  # "retry", "quota_day" or "fatal"
    wait_s: float = 0.0  # suggested wait before retrying
    reason: str = ""


def classify_error(exc: Exception, attempt: int) -> ErrorVerdict:
    """Decide whether an API error is worth retrying. `attempt` is 1 for the first try."""
    status = getattr(exc, "status_code", None)
    text = str(exc)
    # Exponential backoff for the generic case: 2, 4, 8, ... seconds, capped.
    backoff = min(2.0**attempt, MAX_BACKOFF_S)

    if status == 429 or "rate limit" in text.lower():
        # A daily limit will not clear by waiting minutes: stop this model for today.
        if _DAILY_RE.search(text):
            return ErrorVerdict("quota_day", reason=text[:300])
        # A per-minute limit: wait as long as the provider says, plus a small margin.
        match = _RETRY_IN_RE.search(text)
        if match:
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2))
            return ErrorVerdict(
                "retry", min(minutes * 60 + seconds + 0.5, MAX_BACKOFF_S), text[:300]
            )
        return ErrorVerdict("retry", backoff, text[:300])

    if status == 413 or "request too large" in text.lower():
        # The conversation no longer fits the per-minute token limit; waiting cannot help.
        return ErrorVerdict("fatal", reason="request too large: " + text[:300])

    if (
        "tool_use_failed" in text
        or "output_parse_failed" in text
        or "failed to call a function" in text.lower()
    ):
        # The provider could not parse the model's output (Groq: tool_use_failed or
        # output_parse_failed, seen in the M1 pilot). Sampling again usually works, so this
        # is retried like a transient error (it still counts against the quota).
        return ErrorVerdict("retry", 1.0, text[:300])

    if status is not None and status >= 500:
        return ErrorVerdict("retry", backoff, text[:300])

    # Connection problems and timeouts carry no status code.
    name = type(exc).__name__
    if status is None and any(k in name for k in ("Connection", "Timeout", "ServiceUnavailable")):
        return ErrorVerdict("retry", backoff, text[:300])

    return ErrorVerdict("fatal", reason=f"{name}: {text[:300]}")


def estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
    """Rough prompt size in tokens (about 4 characters per token), used by the TPM throttle."""
    size = len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tools))
    return size // 4 + 1


# ---- the real client -----------------------------------------------------------------------


class LiteLLMChat:
    """ChatModel backed by LiteLLM."""

    def __init__(
        self,
        model: ModelConfig,
        temperature: float | None = None,
        gate: RequestGate | None = None,
        usd_to_eur: float | None = None,
        max_tokens: int | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.gate = gate
        self.usd_to_eur = usd_to_eur
        # Read the key explicitly from the variable the config names (SPEC 5.3).
        self.api_key = os.environ.get(model.api_key_env) if model.api_key_env else None
        if model.api_key_env and not self.api_key:
            raise LLMError(f"environment variable {model.api_key_env} is not set")

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        # Imported here: litellm takes a couple of seconds to import, tests never need it.
        import litellm

        kwargs: dict = dict(
            model=self.model.name,
            messages=messages,
            api_key=self.api_key,
            num_retries=0,  # retries are ours, so each real request is counted once
            max_retries=0,  # and the underlying OpenAI client must not retry either
            timeout=120,
        )
        if tools:
            kwargs["tools"] = tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        estimated = estimate_tokens(messages, tools)
        # Time spent waiting for quota, not for the model. The agent loop excludes it from
        # the episode timeout, so a slow free tier never shows up as a model timeout.
        throttle_s = 0.0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Wait for the quota throttle; it raises QuotaExhausted when today's budget is gone.
            if self.gate is not None:
                waited_from = time.monotonic()
                await self.gate.acquire(estimated)
                throttle_s += time.monotonic() - waited_from
            started = time.monotonic()
            try:
                response = await litellm.acompletion(**kwargs)
            except Exception as exc:  # noqa: BLE001 - every provider error is classified below
                verdict = classify_error(exc, attempt)
                if verdict.action == "quota_day":
                    if self.gate is not None:
                        self.gate.mark_day_exhausted()
                    error: LLMError = QuotaExhausted(verdict.reason)
                    error.attempts = attempt
                    raise error from exc
                if verdict.action == "fatal" or attempt == MAX_ATTEMPTS:
                    error = LLMError(f"after {attempt} attempts: {verdict.reason or exc}")
                    error.attempts = attempt
                    raise error from exc
                await asyncio.sleep(verdict.wait_s)
                throttle_s += verdict.wait_s
                continue
            latency_ms = (time.monotonic() - started) * 1000
            reply = self._to_reply(response, latency_ms)
            reply.attempts = attempt
            reply.throttle_s = throttle_s
            if self.gate is not None:
                self.gate.record(reply.tokens_in + reply.tokens_out)
            return reply
        # Not reachable: the loop either returns or raises.
        raise LLMError("no attempts left")

    def _to_reply(self, response, latency_ms: float) -> LLMReply:
        """Turn a LiteLLM response into an LLMReply."""
        first = response.choices[0]
        choice = first.message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (choice.tool_calls or [])
        ]
        content = choice.content or ""
        # The assistant message as it goes back into the history (OpenAI chat format).
        message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in tool_calls
            ]
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        # Model version: the provider's model name plus its fingerprint when it sends one.
        version = response.model or self.model.name
        fingerprint = getattr(response, "system_fingerprint", None)
        if fingerprint:
            version = f"{version}@{fingerprint}"
        reasoning = getattr(choice, "reasoning_content", None) or getattr(choice, "reasoning", None)
        return LLMReply(
            content=content,
            tool_calls=tool_calls,
            message=message,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_eur=self._cost_eur(response, tokens_in, tokens_out),
            model_version=version,
            latency_ms=latency_ms,
            finish_reason=first.finish_reason or "",
            extra={"reasoning": reasoning} if reasoning else {},
        )

    def _cost_eur(self, response, tokens_in: int, tokens_out: int) -> float:
        """Cost of one response in euros. Free-tier models cost nothing by definition."""
        if self.model.free_tier:
            return 0.0
        if self.model.price_usd_per_mtok is not None:
            usd = (tokens_in + tokens_out) * self.model.price_usd_per_mtok / 1_000_000
        else:
            import litellm

            usd = litellm.completion_cost(completion_response=response)
        # config.py guarantees usd_to_eur is set whenever a paid model is configured.
        return usd * (self.usd_to_eur or 0.0)
