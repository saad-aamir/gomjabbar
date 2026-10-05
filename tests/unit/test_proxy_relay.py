"""Tests for the chaos proxy in passthrough mode: byte equality and request tracking."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from gomjabbar.proxy.plan import ProxyPlan
from gomjabbar.proxy.relay import Relay, SideLog

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Lines chosen to be awkward: unicode, escaped newlines, odd spacing, key order, a huge line,
# invalid JSON, and a final line with no newline.
SAMPLE_LINES = [
    b'{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}\n',
    b'{ "jsonrpc" : "2.0", "method":"notifications/initialized" }\n',
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"write_note","arguments":{"text":"Grüße \\n ✓"}}}\n'.encode(),
    b'{"id":2,"jsonrpc":"2.0","method":"tools/list"}\n',
    b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"x","arguments":{"big":"'
    + b"a" * 200_000
    + b'"}}}\n',
    b"this is not json\n",
    b'{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"y"}}',
]


class CaptureWriter:
    """A LineWriter that collects bytes in memory."""

    def __init__(self):
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass


async def _pump_bytes(relay: Relay, data: bytes, direction: str) -> bytes:
    reader = asyncio.StreamReader(limit=10_000_000)
    reader.feed_data(data)
    reader.feed_eof()
    writer = CaptureWriter()
    await relay.pump(reader, writer, direction)
    return writer.data


async def test_pump_is_byte_identical_both_directions():
    data = b"".join(SAMPLE_LINES)
    for direction in ["client_to_server", "server_to_client"]:
        relay = Relay(ProxyPlan(), SideLog(None))
        assert await _pump_bytes(relay, data, direction) == data


async def test_request_tracking_matches_responses():
    relay = Relay(ProxyPlan(), SideLog(None))
    await _pump_bytes(relay, b"".join(SAMPLE_LINES[:5]), "client_to_server")
    # Ids 0..3 are requests; the notification has no id.
    assert relay.pending == {0: "initialize", 1: "tools/call", 2: "tools/list", 3: "tools/call"}
    assert relay.calls_per_tool == {"write_note": 1, "x": 1}
    assert relay.total_calls == 2
    # A response is matched to its request and removed.
    assert relay.match_server_message({"jsonrpc": "2.0", "id": 2, "result": {}}) == "tools/list"
    assert 2 not in relay.pending
    # Server requests (they have a method) are not matched against client ids.
    assert relay.match_server_message({"jsonrpc": "2.0", "id": 1, "method": "roots/list"}) is None
    assert 1 in relay.pending


async def test_unparsed_line_is_logged(tmp_path):
    log = tmp_path / "log.jsonl"
    relay = Relay(ProxyPlan(), SideLog(log))
    await _pump_bytes(relay, b"not json\n", "server_to_client")
    entry = json.loads(log.read_text())
    assert entry["kind"] == "unparsed_line"
    assert set(entry) == {"ts", "kind", "target", "detail"}


def test_proxy_process_is_byte_identical_end_to_end(tmp_path):
    """The real proxy process with an echo server: what goes in comes out, both ways."""
    record = tmp_path / "record.bin"
    env = dict(os.environ, ECHO_RECORD_PATH=str(record))
    data = b"".join(SAMPLE_LINES[:-1])  # every line ends with a newline here
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gomjabbar.proxy",
            "--",
            sys.executable,
            str(FIXTURES / "echo_server.py"),
        ],
        input=data,
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # Client to server: the server received exactly the bytes we sent.
    assert record.read_bytes() == data
    # Server to client: we received exactly the bytes the server sent (it echoed them).
    assert proc.stdout == data
