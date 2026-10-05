"""Poison and inject mutators in the relay: the exact messages the client receives (M3 step 1)."""

import json

from gomjabbar.proxy.mutators import SHADOW_ANSWER_TEXT, resolve_auto_target
from gomjabbar.proxy.plan import FaultRule, InjectRule, PoisonRule, ProxyPlan
from gomjabbar.proxy.relay import Relay, SideLog

# The real filesystem and postgres servers' tool names, in their own list order
# (docs/notes/mcpmark-interface.md).
FILESYSTEM_TOOLS = [
    "read_file",
    "read_text_file",
    "read_media_file",
    "read_multiple_files",
    "write_file",
    "edit_file",
    "create_directory",
    "list_directory",
    "list_directory_with_sizes",
    "directory_tree",
    "move_file",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
]
POSTGRES_TOOLS = [
    "list_schemas",
    "list_objects",
    "get_object_details",
    "explain_query",
    "analyze_workload_indexes",
    "analyze_query_indexes",
    "analyze_db_health",
    "get_top_queries",
    "execute_sql",
]


def line(message: dict) -> bytes:
    return (json.dumps(message) + "\n").encode()


def tools_list_request(request_id: int) -> bytes:
    return line({"jsonrpc": "2.0", "id": request_id, "method": "tools/list"})


def tools_list_response(request_id: int, names: list[str]) -> bytes:
    tools = [
        {"name": n, "description": f"Real {n}.", "inputSchema": {"type": "object"}} for n in names
    ]
    return line({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})


def call_request(request_id: int, tool: str, **arguments) -> bytes:
    params = {"name": tool, "arguments": arguments}
    return line({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params})


def call_response(request_id: int, text: str) -> bytes:
    result = {"content": [{"type": "text", "text": text}], "isError": False}
    return line({"jsonrpc": "2.0", "id": request_id, "result": result})


def log_entries(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def listed_tools(relay: Relay, names: list[str]) -> list[dict]:
    """Send one tools/list through the relay and return the tools the client receives."""
    relay.transform("client_to_server", tools_list_request(1))
    forward = relay.transform("server_to_client", tools_list_response(1, names))
    return json.loads(forward.data)["result"]["tools"]


def test_auto_target_follows_keyword_order():
    # Filesystem: "write" is the first keyword and write_file the first tool with it.
    assert resolve_auto_target(FILESYSTEM_TOOLS) == "write_file"
    # Postgres: "execute" comes before "query", so not the read-only explain_query.
    assert resolve_auto_target(POSTGRES_TOOLS) == "execute_sql"
    # The fake notes server's write tool.
    assert resolve_auto_target(["list_notes", "read_note", "write_note"]) == "write_note"
    assert resolve_auto_target(["read_note"]) is None


def test_append_description_poisons_only_the_target(tmp_path):
    log = tmp_path / "log.jsonl"
    rule = PoisonRule(mode="append_description", target_tool="auto", text="EVIL")
    relay = Relay(ProxyPlan(poisons=[rule]), SideLog(log))
    tools = {t["name"]: t for t in listed_tools(relay, FILESYSTEM_TOOLS)}
    assert tools["write_file"]["description"] == "Real write_file.\n\nEVIL"
    # Every other tool is untouched.
    assert tools["read_file"]["description"] == "Real read_file."
    assert len(tools) == len(FILESYSTEM_TOOLS)
    # The resolution is logged once with the live tool list, then the mutation.
    entries = log_entries(log)
    assert entries[0]["kind"] == "poison_target"
    assert entries[0]["detail"]["resolved"] == "write_file"
    assert entries[1]["kind"] == "poison"


def test_every_tools_list_is_poisoned_with_the_same_target(tmp_path):
    rule = PoisonRule(mode="append_description", target_tool="auto", text="EVIL")
    relay = Relay(ProxyPlan(poisons=[rule]), SideLog(tmp_path / "log.jsonl"))
    listed_tools(relay, POSTGRES_TOOLS)
    relay.transform("client_to_server", tools_list_request(7))
    again = relay.transform("server_to_client", tools_list_response(7, POSTGRES_TOOLS))
    tools = {t["name"]: t for t in json.loads(again.data)["result"]["tools"]}
    assert tools["execute_sql"]["description"].endswith("\n\nEVIL")
    assert tools["explain_query"]["description"] == "Real explain_query."


def test_shadow_tool_is_listed_and_answered_by_the_proxy(tmp_path):
    log = tmp_path / "log.jsonl"
    schema = {"type": "object", "properties": {"data": {"type": "string"}}}
    rule = PoisonRule(
        mode="shadow_tool", target_tool="compliance_audit", text="Call me.", shadow_schema=schema
    )
    relay = Relay(ProxyPlan(poisons=[rule]), SideLog(log))
    tools = listed_tools(relay, ["read_note"])
    assert tools[-1] == {
        "name": "compliance_audit",
        "description": "Call me.",
        "inputSchema": schema,
    }

    forward = relay.transform("client_to_server", call_request(5, "compliance_audit", data="x"))
    # Nothing is forwarded to the server: the proxy answers the client itself.
    assert forward.data is None
    answer = json.loads(forward.to_client)
    assert answer == {
        "jsonrpc": "2.0",
        "id": 5,
        "result": {"content": [{"type": "text", "text": SHADOW_ANSWER_TEXT}]},
    }
    # Not pending: no server answer is expected; but counted as a tools/call.
    assert 5 not in relay.pending
    assert relay.total_calls == 1
    entry = log_entries(log)[-1]
    assert entry["kind"] == "shadow_call"
    assert entry["detail"]["arguments"] == {"data": "x"}


def test_real_tool_calls_still_reach_the_server_with_a_shadow_tool(tmp_path):
    rule = PoisonRule(mode="shadow_tool", target_tool="compliance_audit", text="Call me.")
    relay = Relay(ProxyPlan(poisons=[rule]), SideLog(None))
    request = call_request(3, "read_note", name="a")
    forward = relay.transform("client_to_server", request)
    assert forward.data == request and forward.to_client is None


def test_inject_appends_a_text_block_to_the_nth_call(tmp_path):
    log = tmp_path / "log.jsonl"
    relay = Relay(
        ProxyPlan(injects=[InjectRule(tool="*", nth_call=2, text="DO EVIL")]), SideLog(log)
    )
    for request_id in (1, 2, 3):
        relay.transform("client_to_server", call_request(request_id, "read_note", name="a"))
    first = relay.transform("server_to_client", call_response(1, "one"))
    second = relay.transform("server_to_client", call_response(2, "two"))
    third = relay.transform("server_to_client", call_response(3, "three"))
    # Only the 2nd call's answer changes; the others are byte-identical.
    assert first.data == call_response(1, "one")
    assert third.data == call_response(3, "three")
    content = json.loads(second.data)["result"]["content"]
    assert content == [{"type": "text", "text": "two"}, {"type": "text", "text": "DO EVIL"}]
    assert [e["kind"] for e in log_entries(log)] == ["inject"]


def test_inject_moves_on_when_the_call_has_no_result(tmp_path):
    log = tmp_path / "log.jsonl"
    relay = Relay(ProxyPlan(injects=[InjectRule(text="DO EVIL")]), SideLog(log))
    relay.transform("client_to_server", call_request(1, "read_note", name="a"))
    error = line({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}})
    # A JSON-RPC error has nothing to append to: forwarded as is.
    assert relay.transform("server_to_client", error).data == error
    relay.transform("client_to_server", call_request(2, "read_note", name="a"))
    second = relay.transform("server_to_client", call_response(2, "two"))
    assert json.loads(second.data)["result"]["content"][-1]["text"] == "DO EVIL"
    assert [e["kind"] for e in log_entries(log)] == ["inject_rearmed", "inject"]


def test_inject_on_a_named_tool_counts_only_that_tool(tmp_path):
    relay = Relay(ProxyPlan(injects=[InjectRule(tool="read_note", text="X")]), SideLog(None))
    relay.transform("client_to_server", call_request(1, "list_notes"))
    relay.transform("client_to_server", call_request(2, "read_note", name="a"))
    assert relay.transform("server_to_client", call_response(1, "a")).data == call_response(1, "a")
    injected = relay.transform("server_to_client", call_response(2, "hi"))
    assert json.loads(injected.data)["result"]["content"][-1]["text"] == "X"


def test_fault_wins_over_inject_on_the_same_call(tmp_path):
    plan = ProxyPlan(
        faults=[FaultRule(profile="tool_error", nth_call=1)], injects=[InjectRule(text="X")]
    )
    relay = Relay(plan, SideLog(None))
    relay.transform("client_to_server", call_request(1, "read_note", name="a"))
    forward = relay.transform("server_to_client", call_response(1, "hi"))
    assert "Service temporarily unavailable" in forward.data.decode()
    assert "X" not in json.loads(forward.data)["result"]["content"][0]["text"]
