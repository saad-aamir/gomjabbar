"""The agent loop: a model calling MCP tools through the chaos proxy until it says it is done.

What: `AgentSession` starts the proxy (which starts the real MCP server), lists the tools,
and runs the classic tool-calling loop: ask the model, execute its tool calls, feed the
results back, repeat until the model answers without tool calls or a limit is hit.
Why: Prüfstand needs its own loop (not MCPMark's) so the proxy can sit between agent and
server, and so every step is recorded in a trace the graders can inspect (SPEC 5.3).
How: the episode runner opens an AgentSession, calls `run(task_prompt)`, and gets an
AgentOutcome. The session stays open until the runner leaves the `async with` block, so a
later milestone can add a pushback turn with `continue_with_user_turn` on the same session.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Literal

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from pruefstand.agent.llm import ChatModel, LLMError, QuotaExhausted
from pruefstand.agent.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION
from pruefstand.models import StopReason, TraceEvent, TraceKind

# Tool results longer than this are cut before the model sees them (SPEC 5.3).
MAX_TOOL_RESULT_CHARS = 20_000
TRUNCATION_MARKER = "\n[truncated]"
# The episode timeout counts agent time only (quota waits excluded, see `_steps`). As a
# safety net against hangs, the whole episode is also capped at this multiple of it in
# wall-clock time, quota waits included.
HARD_TIMEOUT_FACTOR = 6
# Prefix of environment variables that must never reach the server under test.
SECRET_ENV_PREFIX = "PFS_"


def server_environment(
    extra: Mapping[str, str] | None = None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Environment for the proxy and the real server: everything except PFS_ variables.

    The full environment is needed because npx relies on the proxy and CA variables
    (docs/notes/mcp-stdio.md). PFS_ variables hold our API keys, so they are removed first.
    `extra` adds per-server variables (for example DATABASE_URI in M2).
    """
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.startswith(SECRET_ENV_PREFIX)}
    env.update(extra or {})
    return env


@dataclass
class ServerLaunch:
    """How to start the real MCP server behind the proxy."""

    command: list[str]  # e.g. ["npx", "-y", "@modelcontextprotocol/server-filesystem@...", dir]
    env_extra: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None


@dataclass
class LoopLimits:
    """Budget for one episode (SPEC 5.3, from the run config)."""

    max_steps: int = 40  # model calls
    tool_timeout_s: float = 30
    episode_timeout_s: float = 900


class Trace:
    """Collects the TraceEvents of one episode, numbering them in order."""

    def __init__(self, episode_id: str, clock=time.time):
        self.episode_id = episode_id
        self.events: list[TraceEvent] = []
        # Injectable clock so tests can produce identical traces.
        self.clock = clock

    def add(self, kind: TraceKind, payload: dict, **numbers) -> TraceEvent:
        event = TraceEvent(
            episode_id=self.episode_id,
            seq=len(self.events),
            ts=self.clock(),
            kind=kind,
            payload=payload,
            **numbers,
        )
        self.events.append(event)
        return event


FinalClaim = Literal["done", "failed", "none"]


def final_claim_of(text: str) -> FinalClaim:
    """done / failed / none from the first word of the last assistant message (SPEC 5.3).

    Leading punctuation and markdown are skipped, so "**DONE**: ..." counts as done.
    """
    match = re.match(r"^\W*([A-Za-z]+)", text or "")
    word = match.group(1).lower() if match else ""
    if word == "done":
        return "done"
    if word == "failed":
        return "failed"
    return "none"


@dataclass
class AgentOutcome:
    """The result of running the loop once (one user turn)."""

    stop_reason: StopReason
    steps: int  # model calls made in this turn
    final_message: str
    final_claim: FinalClaim
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0  # part of tokens_in the provider served from its prompt cache
    cost_eur: float = 0.0
    model_version: str = ""
    # Upstream providers that served this episode's replies. With OpenRouter pinning there
    # is exactly one; more than one would show that the pin did not hold.
    providers: set[str] = field(default_factory=set)
    llm_requests: int = 0  # HTTP requests, retries included (for quota estimates)
    quota_exhausted: bool = False  # the model's daily quota ran out mid-episode
    throttle_s: float = 0.0  # time spent waiting on the quota throttle (not agent time)
    error: str = ""


def mcp_tool_to_openai(tool: types.Tool) -> dict:
    """Convert an MCP tool to the OpenAI function-calling schema the model sees."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def tool_result_text(result: types.CallToolResult) -> str:
    """Flatten an MCP tool result into the text the model sees, truncated if long."""
    parts = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        else:
            # Images, resources and others are shown as their JSON so nothing is hidden.
            parts.append(block.model_dump_json(exclude_none=True))
    text = "\n".join(parts)
    if result.isError:
        text = "Tool error: " + text
    return truncate(text)


def truncate(text: str) -> str:
    """Cut text to MAX_TOOL_RESULT_CHARS with a visible marker."""
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + TRUNCATION_MARKER


class TransportDead(Exception):
    """The MCP session is gone (server exited, pipe closed). Ends the episode."""


# Exceptions that mean the transport itself is broken, not just one call.
_DEAD_TRANSPORT = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


class AgentSession:
    """One MCP session plus the conversation with the model. Use as `async with`."""

    def __init__(
        self,
        launch: ServerLaunch,
        llm: ChatModel,
        limits: LoopLimits,
        trace: Trace,
        plan_path: Path | None = None,
        proxy_log_path: Path | None = None,
        stderr_path: Path | None = None,
    ):
        self.launch = launch
        self.llm = llm
        self.limits = limits
        self.trace = trace
        self.plan_path = plan_path
        self.proxy_log_path = proxy_log_path
        self.stderr_path = stderr_path
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: list[dict] = []  # tool list in OpenAI format
        self.messages: list[dict] = []  # the whole conversation so far
        self._tools_changed = False  # set by notifications/tools/list_changed
        self._started = time.monotonic()
        self._throttle_s = 0.0  # quota waits so far in this session, excluded from agent time

    # ---- session start and stop --------------------------------------------------------

    def proxy_command(self) -> list[str]:
        """The command the MCP client runs: our proxy, which then runs the real server."""
        args = ["-m", "pruefstand.proxy"]
        if self.plan_path is not None:
            args += ["--plan", str(self.plan_path)]
        if self.proxy_log_path is not None:
            args += ["--log", str(self.proxy_log_path)]
        return [sys.executable, *args, "--", *self.launch.command]

    async def __aenter__(self) -> AgentSession:
        self._started = time.monotonic()
        command = self.proxy_command()
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            env=server_environment(self.launch.env_extra),
            cwd=self.launch.cwd,
        )
        # Server stderr goes to a per-episode file instead of cluttering the terminal.
        errlog = (
            self._stack.enter_context(open(self.stderr_path, "a", encoding="utf-8"))
            if self.stderr_path
            else sys.stderr
        )
        try:
            # Entering these contexts starts the SDK's background tasks. They must not sit
            # inside a timeout scope that ends before them (anyio requires strict nesting).
            read, write = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
            self.session = await self._stack.enter_async_context(
                ClientSession(read, write, message_handler=self._on_message)
            )
            # Startup counts against the episode timeout too (npx may be slow).
            with anyio.fail_after(self.limits.episode_timeout_s):
                await self.session.initialize()
                await self._refresh_tools()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        try:
            await self._stack.aclose()
        except Exception as exc:  # noqa: BLE001 - shutting down a dead server may raise
            self.trace.add("error", {"where": "session_close", "error": repr(exc)})

    async def _on_message(self, message) -> None:
        """Handle server notifications; only tools/list_changed matters here."""
        if isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification
        ):
            self._tools_changed = True

    async def _refresh_tools(self) -> None:
        """List tools and convert them for the model. Also fills the SDK's schema cache."""
        listed = await self.session.list_tools()
        self.tools = [mcp_tool_to_openai(tool) for tool in listed.tools]
        self._tools_changed = False

    # ---- the loop ----------------------------------------------------------------------

    async def run(self, task_prompt: str) -> AgentOutcome:
        """Start the conversation with the system prompt and the task, then loop."""
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_prompt},
        ]
        self.trace.add(
            "user_turn",
            {"text": task_prompt, "system_prompt_version": SYSTEM_PROMPT_VERSION},
        )
        return await self._loop(self.limits.max_steps)

    async def continue_with_user_turn(self, text: str, max_steps: int) -> AgentOutcome:
        """Add one more user message to the same session and loop again (pushback, M3)."""
        self.messages.append({"role": "user", "content": text})
        self.trace.add("user_turn", {"text": text})
        return await self._loop(max_steps)

    async def _loop(self, max_steps: int) -> AgentOutcome:
        outcome = AgentOutcome(
            stop_reason="max_steps", steps=0, final_message="", final_claim="none"
        )
        # Hard wall-clock cap (quota waits included), only a safety net against hangs.
        hard_limit = self.limits.episode_timeout_s * HARD_TIMEOUT_FACTOR
        remaining = hard_limit - (time.monotonic() - self._started)
        with anyio.move_on_after(max(remaining, 0)) as scope:
            await self._steps(outcome, max_steps)
        if scope.cancelled_caught:
            outcome.stop_reason = "timeout"
            self.trace.add("error", {"where": "loop", "error": "hard wall-clock timeout"})
        # The claim always comes from the last assistant text, even after a limit was hit.
        outcome.final_claim = final_claim_of(outcome.final_message)
        self.trace.add(
            "end",
            {
                "stop_reason": outcome.stop_reason,
                "final_claim": outcome.final_claim,
                "final_message": outcome.final_message,
                "steps": outcome.steps,
            },
        )
        return outcome

    def agent_seconds(self) -> float:
        """Episode time so far, minus time spent waiting on our quota throttle."""
        return time.monotonic() - self._started - self._throttle_s

    async def _steps(self, outcome: AgentOutcome, max_steps: int) -> None:
        """The body of the loop; fills `outcome` in place so a timeout keeps partial counts."""
        while outcome.steps < max_steps:
            # The episode timeout measures the agent and the server, not the free tier's
            # rate limits, so quota waits are left out (DEVIATIONS.md, 2026-10-02).
            if self.agent_seconds() > self.limits.episode_timeout_s:
                outcome.stop_reason = "timeout"
                self.trace.add("error", {"where": "loop", "error": "episode timeout"})
                return
            # Re-list tools if the server said they changed (pinning defense comes in M3).
            if self._tools_changed:
                await self._refresh_tools()

            # 1. Ask the model.
            self.trace.add(
                "llm_request", {"n_messages": len(self.messages), "n_tools": len(self.tools)}
            )
            try:
                reply = await self.llm.complete(self.messages, self.tools)
            except QuotaExhausted as exc:
                outcome.llm_requests += exc.attempts
                outcome.stop_reason = "llm_error"
                outcome.quota_exhausted = True
                outcome.error = str(exc)
                self.trace.add("error", {"where": "llm", "error": str(exc), "quota": True})
                return
            except LLMError as exc:
                outcome.llm_requests += exc.attempts
                outcome.stop_reason = "llm_error"
                outcome.error = str(exc)
                self.trace.add("error", {"where": "llm", "error": str(exc)})
                return
            outcome.steps += 1
            outcome.tokens_in += reply.tokens_in
            outcome.tokens_out += reply.tokens_out
            outcome.tokens_cached += reply.tokens_cached
            outcome.cost_eur += reply.cost_eur
            if reply.provider:
                outcome.providers.add(reply.provider)
            outcome.llm_requests += reply.attempts
            outcome.model_version = reply.model_version or outcome.model_version
            outcome.throttle_s += reply.throttle_s
            self._throttle_s += reply.throttle_s
            self.trace.add(
                "llm_response",
                {
                    "message": reply.message,
                    "finish_reason": reply.finish_reason,
                    "throttle_s": round(reply.throttle_s, 3),
                    "provider": reply.provider,
                    "tokens_cached": reply.tokens_cached,
                    "cost_eur": reply.cost_eur,
                    **reply.extra,
                },
                tokens_in=reply.tokens_in,
                tokens_out=reply.tokens_out,
                latency_ms=reply.latency_ms,
            )
            self.messages.append(reply.message)
            if reply.content:
                outcome.final_message = reply.content

            # 2. No tool calls means the model has given its final answer.
            if not reply.tool_calls:
                outcome.stop_reason = "final_answer"
                return

            # 3. Execute each tool call and append its result for the model.
            for tool_call in reply.tool_calls:
                try:
                    text = await self._execute(tool_call.name, tool_call.arguments)
                except TransportDead as exc:
                    outcome.stop_reason = "transport_failure"
                    outcome.error = str(exc)
                    self.trace.add("error", {"where": "transport", "error": str(exc)})
                    return
                self.messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": text}
                )
        # Fell out of the while: the step budget is used up.
        outcome.stop_reason = "max_steps"

    async def _execute(self, name: str, raw_arguments: str) -> str:
        """Run one tool call and return the text for the model. Raises TransportDead."""
        # The model's arguments must be a JSON object; otherwise tell it, do not call.
        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            text = f"Tool error: invalid JSON arguments ({exc})"
            self.trace.add("tool_call", {"name": name, "arguments_raw": raw_arguments})
            self.trace.add("tool_result", {"name": name, "text": text, "is_error": True})
            return text

        self.trace.add("tool_call", {"name": name, "arguments": arguments})
        started = time.monotonic()
        try:
            result = await self.session.call_tool(
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=self.limits.tool_timeout_s),
            )
        except McpError as exc:
            if exc.error.code == types.CONNECTION_CLOSED:
                raise TransportDead(str(exc)) from exc
            # JSON-RPC error or call timeout: the model sees it and can react (SPEC 5.3).
            text = f"Tool error: {exc.error.message}"
            self.trace.add(
                "tool_result",
                {"name": name, "text": text, "is_error": True, "rpc_error_code": exc.error.code},
                latency_ms=(time.monotonic() - started) * 1000,
            )
            return text
        except _DEAD_TRANSPORT as exc:
            raise TransportDead(repr(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - e.g. SDK output-schema validation errors
            text = f"Tool error: {exc}"
            self.trace.add(
                "tool_result",
                {"name": name, "text": text, "is_error": True},
                latency_ms=(time.monotonic() - started) * 1000,
            )
            return text

        text = tool_result_text(result)
        self.trace.add(
            "tool_result",
            {"name": name, "text": text, "is_error": bool(result.isError)},
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return text
