"""Integration tests: scripted episodes against the fake MCP server, through the real proxy."""

import json
import sys
from pathlib import Path

from pruefstand.agent.loop import AgentSession, LoopLimits, ServerLaunch, Trace
from tests.fixtures.scripted_llm import ScriptedLLM, call, fail, final, raw_call

FAKE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_server.py"


def launch(tmp_path: Path, **env) -> ServerLaunch:
    """Start the fake server with its notes file in tmp_path."""
    return ServerLaunch(
        command=[sys.executable, str(FAKE_SERVER)],
        env_extra={"FAKE_NOTES_PATH": str(tmp_path / "notes.json"), **env},
    )


async def run_script(tmp_path, steps, limits=None, **env):
    llm = ScriptedLLM(steps)
    trace = Trace("ep1")
    async with AgentSession(
        launch(tmp_path, **env),
        llm,
        limits or LoopLimits(tool_timeout_s=10, episode_timeout_s=60),
        trace,
        proxy_log_path=tmp_path / "proxy.jsonl",
        stderr_path=tmp_path / "stderr.log",
    ) as session:
        tool_names = [t["function"]["name"] for t in session.tools]
        outcome = await session.run("Write a note named a with text hello.")
    return outcome, trace, llm, tool_names


async def test_scripted_episode_through_proxy(tmp_path):
    outcome, trace, llm, tools = await run_script(
        tmp_path,
        [
            call("write_note", name="a", text="hello"),
            call("read_note", name="a"),
            final("DONE wrote it"),
        ],
    )
    # The tools of the fake server reached the model through the proxy.
    assert sorted(tools) == ["delete_note", "list_notes", "read_note", "write_note"]
    # The state changed on the real server.
    assert json.loads((tmp_path / "notes.json").read_text()) == {"a": "hello"}
    assert outcome.stop_reason == "final_answer"
    assert outcome.final_claim == "done"
    assert outcome.steps == 3
    # The model saw the read result as a tool message.
    tool_messages = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert tool_messages[-1]["content"] == "hello"
    kinds = [e.kind for e in trace.events]
    assert kinds[0] == "user_turn" and kinds[-1] == "end"
    assert kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2
    assert [e.seq for e in trace.events] == list(range(len(trace.events)))


async def test_tool_error_is_shown_to_model(tmp_path):
    outcome, trace, llm, _ = await run_script(
        tmp_path, [call("read_note", name="missing"), final("FAILED no such note")]
    )
    tool_message = [m for m in llm.seen[-1] if m["role"] == "tool"][0]
    assert tool_message["content"].startswith("Tool error:")
    assert outcome.final_claim == "failed"


async def test_invalid_arguments_are_reported_not_sent(tmp_path):
    outcome, trace, llm, _ = await run_script(
        tmp_path, [raw_call("write_note", "{not json"), final("DONE")]
    )
    tool_message = [m for m in llm.seen[-1] if m["role"] == "tool"][0]
    assert "invalid JSON arguments" in tool_message["content"]
    assert not (tmp_path / "notes.json").exists()


async def test_max_steps(tmp_path):
    steps = [call("list_notes")] * 5
    outcome, *_ = await run_script(
        tmp_path, steps, LoopLimits(max_steps=3, tool_timeout_s=10, episode_timeout_s=60)
    )
    assert outcome.stop_reason == "max_steps"
    assert outcome.steps == 3
    assert outcome.final_claim == "none"


async def test_llm_error_ends_episode(tmp_path):
    outcome, *_ = await run_script(tmp_path, [call("list_notes"), fail()])
    assert outcome.stop_reason == "llm_error"
    assert outcome.steps == 1


async def test_server_crash_is_transport_failure(tmp_path):
    outcome, trace, *_ = await run_script(
        tmp_path,
        [call("write_note", name="a", text="x"), call("delete_note", name="a"), final("DONE")],
        FAKE_CRASH_ON_DELETE="1",
    )
    assert outcome.stop_reason == "transport_failure"


async def test_server_never_sees_pfs_variables(tmp_path, monkeypatch):
    # A secret in our environment must not reach the server under test (decision 2026-10-02).
    monkeypatch.setenv("PFS_FAKE_SECRET", "sk-should-not-leak")
    monkeypatch.setenv("SOME_ORDINARY_VAR", "1")
    dump = tmp_path / "env.txt"
    await run_script(tmp_path, [final("DONE")], FAKE_ENV_DUMP=str(dump))
    names = dump.read_text().splitlines()
    assert "SOME_ORDINARY_VAR" in names
    assert not [n for n in names if n.startswith("PFS_")]


async def test_quota_waits_do_not_count_toward_episode_timeout(tmp_path):
    # Each model call takes 0.8 s, all of it quota waiting: 3 calls exceed a 1 s timeout in
    # wall-clock time, but not in agent time.
    throttled = call("list_notes")
    throttled.delay_s = throttled.throttle_s = 0.8
    done = final("DONE")
    done.delay_s = done.throttle_s = 0.8
    limits = LoopLimits(tool_timeout_s=10, episode_timeout_s=1.5)
    outcome, *_ = await run_script(tmp_path, [throttled, throttled, done], limits)
    assert outcome.stop_reason == "final_answer"


async def test_slow_agent_times_out(tmp_path):
    slow = call("list_notes")
    slow.delay_s = 0.8  # real model time, no quota wait
    limits = LoopLimits(tool_timeout_s=10, episode_timeout_s=1.5)
    outcome, *_ = await run_script(tmp_path, [slow, slow, slow, final("DONE")], limits)
    assert outcome.stop_reason == "timeout"


async def test_malformed_names_and_parse_retries_are_counted(tmp_path):
    # A tool name with a leaked Harmony token goes to the server unchanged ("unknown tool"),
    # is counted, and is flagged in the trace. Parse retries reported by the client add up.
    retried = call("write_note", name="a", text="hello")
    retried.parse_retries = 2
    outcome, trace, _, _ = await run_script(
        tmp_path,
        [
            raw_call("write_note<|channel|>commentary", '{"name": "a", "text": "hello"}'),
            retried,
            final("DONE"),
        ],
    )
    assert outcome.malformed_tool_names == 1
    assert outcome.parse_retries == 2
    assert outcome.llm_requests == 5  # 3 calls, one of them with 2 extra requests
    flagged = [e for e in trace.events if e.kind == "tool_call" and e.payload.get("malformed_name")]
    assert [e.payload["name"] for e in flagged] == ["write_note<|channel|>commentary"]
    # The server rejected the malformed call; the correct second call wrote the note.
    assert json.loads((tmp_path / "notes.json").read_text()) == {"a": "hello"}


# ---- empty replies are re-sampled (DEVIATIONS.md, 2026-10-03) ----------------------------------


async def test_empty_reply_is_resent_unchanged(tmp_path):
    from tests.fixtures.scripted_llm import empty

    outcome, trace, llm, _ = await run_script(
        tmp_path,
        [
            call("write_note", name="a", text="hello"),
            empty(),  # lost turn: re-sampled, not shown to the model again
            call("read_note", name="a"),
            final("DONE"),
        ],
    )
    # The re-sent request carried exactly the same conversation as the empty one.
    assert llm.seen[1] == llm.seen[2]
    # The empty reply is not part of the conversation afterwards.
    assert all(
        m.get("content") != "" or m.get("tool_calls")
        for m in llm.seen[3][2:]
        if m["role"] == "assistant"
    )
    assert outcome.final_claim == "done"
    assert outcome.steps == 3  # a re-sample is not a step
    assert outcome.llm_requests == 4  # but it is a request
    assert outcome.empty_resamples == 1
    assert outcome.empty_stopped == 1 and outcome.empty_dropped_call == 0
    flagged = [e.payload for e in trace.events if e.payload.get("empty_reply")]
    assert flagged == [{**flagged[0], "empty_reply": "stopped_after_reasoning", "resampled": True}]


async def test_empty_reply_ends_the_episode_after_three_resamples(tmp_path):
    from tests.fixtures.scripted_llm import empty

    outcome, trace, llm, _ = await run_script(
        tmp_path, [empty(tokens_out=60), empty(), empty(tokens_out=60), empty(), final("DONE")]
    )
    # Three re-samples, then the fourth empty reply is the final answer, as before.
    assert outcome.stop_reason == "final_answer"
    assert outcome.final_claim == "none"
    assert outcome.steps == 1
    assert outcome.empty_resamples == 3
    # Every empty reply is classified: 60 output tokens with a short reasoning is a lost call.
    assert outcome.empty_dropped_call == 2 and outcome.empty_stopped == 2
    assert len(llm.seen) == 4  # the scripted DONE was never asked for
    resampled = [e.payload["resampled"] for e in trace.events if "empty_reply" in e.payload]
    assert resampled == [True, True, True, False]
