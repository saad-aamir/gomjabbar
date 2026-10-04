"""The relay at the heart of the chaos proxy.

What: copies newline-delimited JSON-RPC messages between the MCP client (our stdin/stdout)
and the real server (a subprocess), byte for byte, while tracking which response answers
which request.
Why: MCP stdio frames are one UTF-8 JSON message per line (docs/notes/mcp-stdio.md). Working
on raw lines instead of an SDK server lets the proxy forward untouched traffic exactly, which
keeps baseline faithful, and later lets it emit deliberately broken messages.
How: `Relay.pump` reads one direction line by line. Each line is parsed on a copy only to look
at `method` and `id`; the original bytes are what gets written. The client to server pump
records `id -> method` for requests and decides whether a fault or inject rule fires on a
tools/call; a call of a shadow tool is answered by the proxy itself and never forwarded. The
server to client pump looks up responses and lets mutators.py rewrite a faulted or injected
tools/call answer, or a poisoned tools/list answer. With an empty plan nothing changes.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Protocol

from pruefstand.proxy.mutators import (
    Forward,
    apply_fault,
    inject_text,
    poison_tools,
    resolve_target,
    shadow_answer,
    tools_list_line,
)
from pruefstand.proxy.plan import FaultRule, ProxyPlan

# Largest single message the relay accepts. MCP results (file contents) can be big; asyncio's
# default of 64 KiB per line would break on them.
MAX_LINE_BYTES = 64 * 1024 * 1024


class LineWriter(Protocol):
    """The part of asyncio.StreamWriter the relay needs (lets tests pass a fake)."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...


class SideLog:
    """The proxy's side-channel log: one JSON line per event, {ts, kind, target, detail}."""

    def __init__(self, path: Path | None):
        # No path means logging is off (used by some unit tests).
        self.path = path

    def write(self, kind: str, target: str, detail: dict) -> None:
        if self.path is None:
            return
        entry = {"ts": time.time(), "kind": kind, "target": target, "detail": detail}
        # Opened per write so every line is on disk even if the proxy is killed.
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def parse_line(line: bytes) -> dict | None:
    """Parse a copy of one line as a JSON object, or return None if it is not one."""
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None


class Relay:
    """Request tracking and line forwarding for one proxy session."""

    def __init__(self, plan: ProxyPlan, log: SideLog, client_writer: LineWriter | None = None):
        self.plan = plan
        self.log = log
        # Where the proxy's own messages to the client go (shadow tool answers). The proxy
        # entry point passes its stdout; unit tests read Forward.to_client instead.
        self.client_writer = client_writer
        # Client request id -> method, removed when the response arrives.
        self.pending: dict[object, str] = {}
        # Count of tools/call requests per tool name, and in total (used by mutators in M2).
        self.calls_per_tool: Counter[str] = Counter()
        self.total_calls = 0
        # Request id -> fault rule whose response must be altered (decided at request time).
        self.faulted: dict[object, FaultRule] = {}
        # Indexes of fault rules that already fired; each fires once (SPEC 5.2).
        self.fired: set[int] = set()
        # Names of the shadow tools the plan adds; calls to them never reach the server.
        self.shadow_names = {p.target_tool for p in plan.poisons if p.mode == "shadow_tool"}
        # Request id -> index of the inject rule waiting for that call's response.
        self.inject_pending: dict[object, int] = {}
        # Indexes of inject rules whose text was delivered (each is delivered once).
        self.injected: set[int] = set()
        # Poison rule index -> resolved real tool, filled at the first tools/list response.
        self.poison_targets: dict[int, str | None] | None = None

    # ---- tracking ----------------------------------------------------------------------

    def track_client_message(self, message: dict) -> None:
        """Remember a client request so its response can be identified later."""
        method = message.get("method")
        if method is None or "id" not in message:
            # Notifications (no id) and client responses to server requests are not tracked.
            return
        self.pending[message["id"]] = method
        if method == "tools/call":
            tool = (message.get("params") or {}).get("name", "")
            self.calls_per_tool[tool] += 1
            self.total_calls += 1
            self._pick_fault(message["id"], tool)
            self._pick_inject(message["id"], tool)

    def _pick_fault(self, request_id: object, tool: str) -> None:
        """Mark this tools/call for a fault if a rule matches its position. The request
        itself is still forwarded: the real server executes the call in every profile."""
        for index, rule in enumerate(self.plan.faults):
            if index in self.fired:
                continue
            # "*" counts every tools/call; a named tool counts only its own calls.
            count = self.total_calls if rule.tool == "*" else self.calls_per_tool[rule.tool]
            if (rule.tool == "*" or rule.tool == tool) and count == rule.nth_call:
                self.fired.add(index)
                self.faulted[request_id] = rule
                return

    def _pick_inject(self, request_id: object, tool: str) -> None:
        """Arm an inject rule for this tools/call. A rule matches its nth matching call or,
        if that answer was an error with no result to append to, the next one after it
        (decision C, DEVIATIONS.md 2026-10-03), so every inject episode delivers the text."""
        waiting = set(self.inject_pending.values())
        for index, rule in enumerate(self.plan.injects):
            if index in self.injected or index in waiting:
                continue
            count = self.total_calls if rule.tool == "*" else self.calls_per_tool[rule.tool]
            if (rule.tool == "*" or rule.tool == tool) and count >= rule.nth_call:
                self.inject_pending[request_id] = index
                return

    def shadow_call(self, message: dict) -> Forward | None:
        """If this client message calls a shadow tool, the proxy's answer; otherwise None.

        The call is counted like any tools/call (so later rules see the true position) but
        not forwarded and not tracked as pending: the real server never learns of it."""
        if message.get("method") != "tools/call" or "id" not in message:
            return None
        tool = (message.get("params") or {}).get("name", "")
        if tool not in self.shadow_names:
            return None
        self.calls_per_tool[tool] += 1
        self.total_calls += 1
        self.log.write(
            "shadow_call",
            tool,
            {
                "request_id": message["id"],
                "arguments": (message.get("params") or {}).get("arguments"),
            },
        )
        return Forward(None, to_client=shadow_answer(message["id"]))

    def match_server_message(self, message: dict) -> str | None:
        """For a server response, return the method of the request it answers."""
        if "method" in message or "id" not in message:
            # Server requests (roots/list, sampling) and notifications use their own id space.
            return None
        return self.pending.pop(message["id"], None)

    # ---- forwarding --------------------------------------------------------------------

    def transform(self, direction: str, line: bytes) -> Forward:
        """What to forward for one line: the same bytes unless a fault rule fires on it."""
        message = parse_line(line)
        if message is None:
            # Not JSON: forward unchanged and note it, since the spec forbids it on stdio.
            if line.strip():
                self.log.write("unparsed_line", direction, {"bytes": len(line)})
            return Forward(line)
        if direction == "client_to_server":
            shadow = self.shadow_call(message)
            if shadow is not None:
                return shadow
            self.track_client_message(message)
            return Forward(line)
        method = self.match_server_message(message)
        if method == "tools/list" and self.plan.poisons:
            return self._poison_list(message, line)
        if method != "tools/call":
            return Forward(line)
        request_id = message.get("id")
        rule = self.faulted.pop(request_id, None)
        if rule is not None:
            return self._fault(rule, message, line)
        inject_index = self.inject_pending.pop(request_id, None)
        if inject_index is not None:
            return self._inject(inject_index, message, line)
        return Forward(line)

    def _fault(self, rule: FaultRule, message: dict, line: bytes) -> Forward:
        """Apply a fault rule to the response of the call it was picked for."""
        forward = apply_fault(rule, message, line)
        # One side-log line per mutation: {ts, kind, target, detail} (SPEC 5.2).
        self.log.write(
            "fault",
            rule.tool,
            {
                "profile": rule.profile,
                "request_id": message.get("id"),
                "nth_call": rule.nth_call,
                "swallowed": forward.data is None,
                "delay_s": forward.delay_s,
            },
        )
        return forward

    def _inject(self, index: int, message: dict, line: bytes) -> Forward:
        """Append the inject rule's text to this result, or re-arm it if there is no result."""
        rule = self.plan.injects[index]
        if not isinstance(message.get("result"), dict):
            # A JSON-RPC error has no content to append to: wait for the next matching call.
            self.log.write("inject_rearmed", rule.tool, {"request_id": message.get("id")})
            return Forward(line)
        self.injected.add(index)
        self.log.write(
            "inject",
            rule.tool,
            {"request_id": message.get("id"), "nth_call": rule.nth_call, "text": rule.text},
        )
        return Forward(inject_text(message, rule.text))

    def _poison_list(self, message: dict, line: bytes) -> Forward:
        """Rewrite a tools/list answer with every poison rule of the plan."""
        tools = (message.get("result") or {}).get("tools")
        if not isinstance(tools, list):
            return Forward(line)
        names = [tool.get("name", "") for tool in tools]
        if self.poison_targets is None:
            # Resolve the targets once, from the first live tool list, and log the choice
            # (SPEC 6.4): later lists keep the same target even if the list changes.
            self.poison_targets = {}
            for index, rule in enumerate(self.plan.poisons):
                if rule.mode == "shadow_tool":
                    continue
                target = resolve_target(rule, names)
                self.poison_targets[index] = target
                self.log.write(
                    "poison_target",
                    rule.target_tool,
                    {"rule": index, "mode": rule.mode, "resolved": target, "tools": names},
                )
        poisoned = poison_tools(self.plan.poisons, tools, self.poison_targets)
        self.log.write(
            "poison",
            "tools/list",
            {
                "request_id": message.get("id"),
                "rules": [
                    {"mode": rule.mode, "target": self.poison_targets.get(i, rule.target_tool)}
                    for i, rule in enumerate(self.plan.poisons)
                ],
            },
        )
        return Forward(tools_list_line(message, poisoned))

    async def pump(self, reader: asyncio.StreamReader, writer: LineWriter, direction: str) -> None:
        """Copy lines from reader to writer until end of input, applying the plan."""
        while True:
            # readline keeps the trailing b"\n"; at EOF it returns the rest (maybe no newline).
            line = await reader.readline()
            if not line:
                return
            forward = self.transform(direction, line)
            if forward.to_client is not None and self.client_writer is not None:
                # The proxy's own answer (shadow tool): back to the client, not onward.
                self.client_writer.write(forward.to_client)
                await self.client_writer.drain()
            if forward.delay_s:
                await asyncio.sleep(forward.delay_s)
            if forward.data is None:
                # Swallowed (timeout profile): the client never gets this message.
                continue
            writer.write(forward.data)
            await writer.drain()
