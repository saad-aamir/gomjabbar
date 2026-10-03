"""The relay at the heart of the chaos proxy.

What: copies newline-delimited JSON-RPC messages between the MCP client (our stdin/stdout)
and the real server (a subprocess), byte for byte, while tracking which response answers
which request.
Why: MCP stdio frames are one UTF-8 JSON message per line (docs/notes/mcp-stdio.md). Working
on raw lines instead of an SDK server lets the proxy forward untouched traffic exactly, which
keeps baseline faithful, and later lets it emit deliberately broken messages.
How: `Relay.pump` reads one direction line by line. Each line is parsed on a copy only to look
at `method` and `id`; the original bytes are what gets written. The client to server pump
records `id -> method` for requests; the server to client pump looks up responses. M2 adds
mutators that may replace a line before it is written; with an empty plan nothing changes.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Protocol

from pruefstand.proxy.plan import ProxyPlan

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

    def __init__(self, plan: ProxyPlan, log: SideLog):
        self.plan = plan
        self.log = log
        # Client request id -> method, removed when the response arrives.
        self.pending: dict[object, str] = {}
        # Count of tools/call requests per tool name, and in total (used by mutators in M2).
        self.calls_per_tool: Counter[str] = Counter()
        self.total_calls = 0

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

    def match_server_message(self, message: dict) -> str | None:
        """For a server response, return the method of the request it answers."""
        if "method" in message or "id" not in message:
            # Server requests (roots/list, sampling) and notifications use their own id space.
            return None
        return self.pending.pop(message["id"], None)

    # ---- forwarding --------------------------------------------------------------------

    def transform(self, direction: str, line: bytes) -> bytes:
        """Return the bytes to forward for one line. Passthrough in M1: always the same bytes."""
        message = parse_line(line)
        if message is None:
            # Not JSON: forward unchanged and note it, since the spec forbids it on stdio.
            if line.strip():
                self.log.write("unparsed_line", direction, {"bytes": len(line)})
            return line
        if direction == "client_to_server":
            self.track_client_message(message)
        else:
            self.match_server_message(message)
        return line

    async def pump(self, reader: asyncio.StreamReader, writer: LineWriter, direction: str) -> None:
        """Copy lines from reader to writer until end of input."""
        while True:
            # readline keeps the trailing b"\n"; at EOF it returns the rest (maybe no newline).
            line = await reader.readline()
            if not line:
                return
            out = self.transform(direction, line)
            writer.write(out)
            await writer.drain()
