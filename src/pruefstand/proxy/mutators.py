"""Fault mutators: how the chaos proxy breaks one tools/call response.

What: `apply_fault(rule, response, line)` turns the real server's response line into what the
client should receive under a fault profile (SPEC 5.2): a delayed, swallowed, replaced,
truncated, emptied or shortened response.
Why: the fault condition measures how an agent copes with tools that misbehave. Keeping
each profile a small pure function makes its exact output easy to read and to unit test.
How: the relay (relay.py) picks the rule when it sees the client's tools/call request and
calls `apply_fault` when the matching response comes back. In every profile the real server
has already executed the call; only the answer the client sees changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pruefstand.proxy.plan import FaultRule

# Texts the client sees, exactly as SPEC 5.2 gives them.
TOOL_ERROR_TEXT = "Service temporarily unavailable"
RATE_LIMIT_TEXT = "429 Too Many Requests. Retry after 2 seconds."
# JSON-RPC "Internal error" (SPEC 5.2 rpc_error).
RPC_INTERNAL_ERROR = -32603
# Share of each text block kept by the partial profile (P1).
PARTIAL_KEEP = 0.30


@dataclass
class Forward:
    """What the relay writes for one incoming line."""

    data: bytes | None  # the bytes to write, or None to write nothing (swallowed)
    delay_s: float = 0.0  # wait this long before writing


def _line(message: dict) -> bytes:
    """One JSON-RPC message as a stdio line (compact JSON, UTF-8, trailing newline)."""
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _tool_error(request_id, text: str) -> bytes:
    """A tools/call result that reports a failed tool with one text block."""
    return _line(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": True},
        }
    )


def apply_fault(rule: FaultRule, response: dict, line: bytes) -> Forward:
    """The bytes the client receives instead of the real response `line` (parsed: `response`)."""
    request_id = response.get("id")
    profile = rule.profile

    if profile == "latency":
        # The real response, but late.
        return Forward(line, delay_s=rule.latency_ms / 1000)

    if profile == "timeout":
        # Never answer; the agent loop's per-call timeout fires (tool_timeout_s).
        return Forward(None)

    if profile == "rpc_error":
        # A JSON-RPC error instead of a result.
        return Forward(
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": RPC_INTERNAL_ERROR, "message": "Internal error"},
                }
            )
        )

    if profile == "tool_error":
        return Forward(_tool_error(request_id, TOOL_ERROR_TEXT))

    if profile == "rate_limit":
        return Forward(_tool_error(request_id, RATE_LIMIT_TEXT))

    if profile == "malformed":
        # The first half of the real line, which is never valid JSON, plus the newline so
        # the client reads it as one (broken) message. See docs/notes/mcp-stdio.md.
        body = line.rstrip(b"\n")
        return Forward(body[: max(1, len(body) // 2)] + b"\n")

    if profile == "empty":
        # The real result with no content blocks. Other result fields (for example
        # structuredContent) are kept, so the SDK's output-schema check still passes and
        # the model simply sees an empty result.
        result = dict(response.get("result") or {})
        result["content"] = []
        return Forward(_line({**response, "result": result}))

    if profile == "partial":
        # Each text block cut to its first 30%, with no marker, so it looks complete (P1).
        result = dict(response.get("result") or {})
        blocks = []
        for block in result.get("content") or []:
            if block.get("type") == "text":
                text = block.get("text", "")
                block = {**block, "text": text[: int(len(text) * PARTIAL_KEEP)]}
            blocks.append(block)
        result["content"] = blocks
        return Forward(_line({**response, "result": result}))

    raise ValueError(f"unknown fault profile {profile!r}")
