"""Entry point: `python -m gomjabbar.proxy --plan P --log L -- <server command...>`.

What: starts the real MCP server as a subprocess and relays stdin/stdout both ways.
Why: the agent's MCP client launches this module instead of the server, so every message
passes through the relay (SPEC 5.2) without the client knowing.
How: our stdin and stdout are wired to asyncio streams; two Relay.pump tasks copy the two
directions. The server's stderr goes straight to our stderr. When the client closes our
stdin we close the server's stdin and wait for it; when the server exits we exit too.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from gomjabbar.proxy.plan import ProxyPlan, load_plan
from gomjabbar.proxy.relay import MAX_LINE_BYTES, Relay, SideLog


class _StdoutWriter:
    """Writes bytes to our stdout file descriptor (a LineWriter for the relay)."""

    def write(self, data: bytes) -> None:
        # sys.stdout.buffer is blocking, which is fine: the client reads it promptly,
        # and we flush after each line so nothing sits in a buffer.
        sys.stdout.buffer.write(data)

    async def drain(self) -> None:
        sys.stdout.buffer.flush()


async def _stdin_reader() -> asyncio.StreamReader:
    """Wrap our stdin in an asyncio StreamReader."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_LINE_BYTES)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader


async def run_proxy(plan: ProxyPlan, log_path: Path | None, command: list[str]) -> int:
    """Run the relay until either side finishes. Returns the server's exit code."""
    # One stdout writer shared by both directions: the relay writes its own answers to
    # shadow tool calls to the client while it handles the client's lines.
    client_out = _StdoutWriter()
    relay = Relay(plan, SideLog(log_path), client_writer=client_out)
    # Start the real server. env=None means it inherits our environment, which the agent
    # loop already stripped of PFS_ variables. stderr=None means it shares our stderr.
    server = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,
        limit=MAX_LINE_BYTES,
    )
    client_in = await _stdin_reader()
    # If the client terminates us (stdio_client does on shutdown), stop the server too,
    # so no orphaned server process outlives the episode.
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, server.terminate)

    async def client_to_server() -> None:
        # Forward the client's messages; when the client hangs up, close the server's stdin.
        await relay.pump(client_in, server.stdin, "client_to_server")
        server.stdin.close()

    async def server_to_client() -> None:
        await relay.pump(server.stdout, client_out, "server_to_client")

    up = asyncio.create_task(client_to_server())
    down = asyncio.create_task(server_to_client())
    # The session is over when the server's stdout closes (server exited or finished).
    await down
    up.cancel()
    return await server.wait()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m gomjabbar.proxy")
    parser.add_argument("--plan", type=Path, default=None, help="plan.json; none = passthrough")
    parser.add_argument("--log", type=Path, default=None, help="side-channel log (JSON lines)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- then the server command")
    args = parser.parse_args()
    # argparse keeps the "--" separator in REMAINDER; drop it.
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing server command after --")
    plan = load_plan(args.plan) if args.plan else ProxyPlan()
    code = asyncio.run(run_proxy(plan, args.log, command))
    # Flush and exit with the server's code so the client sees the same outcome.
    sys.stdout.flush()
    os._exit(code if code >= 0 else 1)


if __name__ == "__main__":
    main()
