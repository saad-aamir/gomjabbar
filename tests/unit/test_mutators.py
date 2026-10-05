"""Every fault profile: the exact message the client receives (SPEC 5.2, M2 acceptance)."""

import asyncio
import json

import pytest

from gomjabbar.proxy.plan import FaultRule, ProxyPlan
from gomjabbar.proxy.relay import Relay, SideLog


def request(request_id: int, tool: str) -> bytes:
    message = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": {"name": "a"}},
    }
    return (json.dumps(message) + "\n").encode()


# The real server's answer to the second call: one text block plus structured content.
REAL_RESPONSE = (
    b'{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"0123456789"}],'
    b'"structuredContent":{"result":"0123456789"},"isError":false}}\n'
)
FIRST_RESPONSE = b'{"jsonrpc":"2.0","id":1,"result":{"content":[],"isError":false}}\n'


def run_profile(profile: str, tmp_path, **rule_fields):
    """Two tools/call requests and their responses through a relay with one fault rule.

    Returns (what the client got for call 1, Forward for call 2, side-log entries)."""
    log = tmp_path / "log.jsonl"
    relay = Relay(ProxyPlan(faults=[FaultRule(profile=profile, **rule_fields)]), SideLog(log))
    # Both requests reach the server unchanged (it executes both calls).
    assert relay.transform("client_to_server", request(1, "read_note")).data == request(
        1, "read_note"
    )
    assert relay.transform("client_to_server", request(2, "read_note")).data == request(
        2, "read_note"
    )
    first = relay.transform("server_to_client", FIRST_RESPONSE)
    second = relay.transform("server_to_client", REAL_RESPONSE)
    entries = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return first, second, entries


def parsed(forward) -> dict:
    return json.loads(forward.data)


def test_first_call_is_untouched_and_mutation_is_logged(tmp_path):
    first, _, entries = run_profile("tool_error", tmp_path)
    assert first.data == FIRST_RESPONSE and first.delay_s == 0
    assert len(entries) == 1
    assert entries[0]["kind"] == "fault" and entries[0]["target"] == "*"
    assert entries[0]["detail"]["profile"] == "tool_error"
    assert entries[0]["detail"]["request_id"] == 2


def test_latency(tmp_path):
    _, second, _ = run_profile("latency", tmp_path, latency_ms=1500)
    assert second.data == REAL_RESPONSE  # the real answer ...
    assert second.delay_s == 1.5  # ... 1.5 s late


def test_timeout(tmp_path):
    _, second, entries = run_profile("timeout", tmp_path)
    assert second.data is None  # nothing is ever sent
    assert entries[0]["detail"]["swallowed"] is True


def test_rpc_error(tmp_path):
    _, second, _ = run_profile("rpc_error", tmp_path)
    assert second.data == (
        b'{"jsonrpc":"2.0","id":2,"error":{"code":-32603,"message":"Internal error"}}\n'
    )


def test_tool_error(tmp_path):
    _, second, _ = run_profile("tool_error", tmp_path)
    assert second.data == (
        b'{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",'
        b'"text":"Service temporarily unavailable"}],"isError":true}}\n'
    )


def test_rate_limit(tmp_path):
    _, second, _ = run_profile("rate_limit", tmp_path)
    assert second.data == (
        b'{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",'
        b'"text":"429 Too Many Requests. Retry after 2 seconds."}],"isError":true}}\n'
    )


def test_malformed(tmp_path):
    _, second, _ = run_profile("malformed", tmp_path)
    body = REAL_RESPONSE.rstrip(b"\n")
    assert second.data == body[: len(body) // 2] + b"\n"
    with pytest.raises(ValueError):
        json.loads(second.data)


def test_empty(tmp_path):
    _, second, _ = run_profile("empty", tmp_path)
    assert parsed(second) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [],
            "structuredContent": {"result": "0123456789"},
            "isError": False,
        },
    }


def test_partial(tmp_path):
    _, second, _ = run_profile("partial", tmp_path)
    # 30% of a 10-character block is its first 3 characters, with no marker.
    assert parsed(second)["result"]["content"] == [{"type": "text", "text": "012"}]


def test_named_tool_counts_only_its_own_calls(tmp_path):
    relay = Relay(
        ProxyPlan(faults=[FaultRule(profile="timeout", tool="write_note")]), SideLog(None)
    )
    relay.transform("client_to_server", request(1, "write_note"))
    relay.transform("client_to_server", request(2, "read_note"))
    relay.transform("client_to_server", request(3, "write_note"))  # 2nd write_note call
    assert relay.transform("server_to_client", REAL_RESPONSE).data is not None  # id 2
    third = REAL_RESPONSE.replace(b'"id":2', b'"id":3')
    assert relay.transform("server_to_client", third).data is None


def test_rule_fires_once(tmp_path):
    relay = Relay(ProxyPlan(faults=[FaultRule(profile="timeout", nth_call=1)]), SideLog(None))
    for i in (1, 2):
        relay.transform("client_to_server", request(i, "read_note"))
    assert relay.transform("server_to_client", FIRST_RESPONSE).data is None
    assert relay.transform("server_to_client", REAL_RESPONSE).data == REAL_RESPONSE


async def test_pump_waits_and_swallows():
    class Capture:
        data = b""

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

    relay = Relay(ProxyPlan(faults=[FaultRule(profile="timeout", nth_call=1)]), SideLog(None))
    relay.transform("client_to_server", request(1, "read_note"))
    reader = asyncio.StreamReader()
    reader.feed_data(FIRST_RESPONSE + b"plain line\n")
    reader.feed_eof()
    out = Capture()
    await relay.pump(reader, out, "server_to_client")
    assert out.data == b"plain line\n"  # the faulted response was swallowed
