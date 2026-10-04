"""Integration tests: the three defenses inside the agent loop, through the real proxy.

Each test runs a scripted episode against the fake MCP server with a poison or inject plan
and one defense switched on, and checks what the model was shown, what reached the server,
and the defense_action events in the trace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pruefstand.agent.loop import AgentSession, LoopLimits, ServerLaunch, Trace
from pruefstand.defenses.pinning import ToolPin
from pruefstand.defenses.sanitizer import REMOVED_MARKER
from pruefstand.payloads import load_payloads
from pruefstand.proxy.plan import InjectRule, PoisonRule, ProxyPlan, save_plan
from tests.fixtures.scripted_llm import ScriptedLLM, call, final

FAKE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_server.py"


async def run_defended(tmp_path, plan, steps, defenses, before_run=None):
    """One scripted episode with a proxy plan and the given defenses."""
    plan_path = tmp_path / "plan.json"
    save_plan(plan, plan_path)
    llm = ScriptedLLM(steps)
    trace = Trace("ep1")
    async with AgentSession(
        ServerLaunch(
            command=[sys.executable, str(FAKE_SERVER)],
            env_extra={"FAKE_NOTES_PATH": str(tmp_path / "notes.json")},
        ),
        llm,
        LoopLimits(tool_timeout_s=5, episode_timeout_s=60),
        trace,
        plan_path=plan_path,
        proxy_log_path=tmp_path / "proxy.jsonl",
        stderr_path=tmp_path / "stderr.log",
        defenses=defenses,
    ) as session:
        if before_run:
            before_run(session)
        tools = {t["function"]["name"]: t["function"] for t in session.tools}
        outcome = await session.run("Write a note.")
    actions = [e.payload for e in trace.events if e.kind == "defense_action"]
    return outcome, llm, tools, actions


def payload(kind, payload_id):
    return next(p for p in load_payloads(kind) if p.id == payload_id)


async def test_no_defenses_changes_nothing(tmp_path):
    # The same poison without defenses reaches the model unchanged (M3 behaviour).
    text = payload("poisoning", "marker-fs").text
    plan = ProxyPlan(poisons=[PoisonRule(mode="append_description", target_tool="auto", text=text)])
    outcome, _, tools, actions = await run_defended(tmp_path, plan, [final("DONE")], [])
    assert tools["write_note"]["description"].endswith(text)
    assert actions == [] and outcome.defense_actions == 0


async def test_description_scan_strips_appended_poison(tmp_path):
    text = payload("poisoning", "marker-fs").text
    plan = ProxyPlan(poisons=[PoisonRule(mode="append_description", target_tool="auto", text=text)])
    outcome, llm, tools, actions = await run_defended(
        tmp_path, plan, [final("DONE")], ["description_scan"]
    )
    # The model's tool list carries no payload sentence.
    shown = json.dumps(llm.seen_tools[0])
    assert "maintainers" not in shown
    assert "SYNC_OK" not in json.dumps(tools)
    assert {a["defense"] for a in actions} == {"description_scan"}
    assert outcome.defense_actions == len(actions) > 0


async def test_description_scan_hides_shadow_tool_and_blocks_calls(tmp_path):
    shadow = payload("poisoning", "shadow-audit")
    rule = PoisonRule(
        mode="shadow_tool",
        target_tool=shadow.target_tool,
        text=shadow.text,
        shadow_schema=shadow.shadow_schema,
    )
    outcome, llm, tools, actions = await run_defended(
        tmp_path,
        ProxyPlan(poisons=[rule]),
        # The scripted model calls the shadow tool by name anyway.
        [call("compliance_audit", data="secret"), final("DONE")],
        ["description_scan"],
    )
    assert "compliance_audit" not in tools
    # The host refused the call; the proxy never answered it ("ok" would mean it did).
    shown = [m for m in llm.seen[1] if m["role"] == "tool"][-1]["content"]
    assert shown == "Tool error: unknown tool compliance_audit"
    log = (tmp_path / "proxy.jsonl").read_text() if (tmp_path / "proxy.jsonl").exists() else ""
    assert "shadow_call" not in log
    assert [a["action"] for a in actions][-2:] == ["hidden_tool", "blocked_call"]


async def test_sanitizer_strips_injected_block(tmp_path):
    text = payload("injection", "system-override").text
    plan = ProxyPlan(injects=[InjectRule(tool="*", nth_call=1, text=text)])
    outcome, llm, _, actions = await run_defended(
        tmp_path, plan, [call("list_notes"), final("DONE")], ["sanitizer"]
    )
    shown = [m for m in llm.seen[1] if m["role"] == "tool"][-1]["content"]
    assert shown == "(no notes)\n" + REMOVED_MARKER
    assert [a["action"] for a in actions] == ["removed_line"]
    assert actions[0]["text"] == text
    assert outcome.defense_actions == 1


async def test_pinning_keeps_original_tools_after_a_change(tmp_path):
    # No rug pull exists, so the "change" is simulated: the pin is replaced with a different
    # list, then a re-listing is forced. The model must see the pinned list, not the server's.
    def tamper(session):
        pinned = json.loads(json.dumps(session.tools))
        pinned[0]["function"]["description"] = "PINNED"
        session._pin = ToolPin(pinned)
        session._tools_changed = True

    outcome, llm, _, actions = await run_defended(
        tmp_path, ProxyPlan(), [final("DONE")], ["pinning"], before_run=tamper
    )
    assert "PINNED" in json.dumps(llm.seen_tools[0])
    assert [a["change"] for a in actions] == ["changed"]
    assert outcome.defense_actions == 1
