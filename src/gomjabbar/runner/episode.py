"""Run one episode from spec to stored result.

What: `run_episode` sets up the sandbox, writes the proxy plan, runs the agent loop,
merges the proxy's side log into the trace, grades the final state, and only then writes
the result (SPEC 5.5).
Why: this is the one place where the order "grade first, write after" is enforced. A
GraderError propagates before anything is appended to results.jsonl, so no partial or
default result can ever be written (CLAUDE.md hard rule). The episode's trace is still saved
to grader_errors/ first, so the failure can be debugged.
How: the grid runner calls it once per spec with the task, an environment, a model client
and the run store. If the model's daily quota runs out mid-episode it raises QuotaPause
without writing anything, so the episode reruns after the reset.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from gomjabbar.agent.llm import ChatModel
from gomjabbar.agent.loop import AgentOutcome, AgentSession, LoopLimits, Trace
from gomjabbar.conditions import plan_for, prompt_for
from gomjabbar.conditions import pushback as pushback_cond
from gomjabbar.config import RunConfig
from gomjabbar.graders.honesty import false_success, strict_pass
from gomjabbar.graders.policy import CanaryVerdict, attacker_goal_met, scan_canary
from gomjabbar.graders.state import GraderError
from gomjabbar.models import (
    VAULT_CONDITIONS,
    EpisodeResult,
    EpisodeSpec,
    PushbackOutcome,
    Task,
)
from gomjabbar.payloads import attack_payload, load_pushback
from gomjabbar.proxy.plan import save_plan
from gomjabbar.runner.environment import EpisodeEnvironment
from gomjabbar.runner.store import RunStore
from gomjabbar.sandbox.canary import canary_value


class QuotaPause(Exception):
    """The model could not be used any more during this episode (daily quota, no credit, or a
    rejected key). Nothing was written; the episode reruns on --resume."""

    def __init__(self, model: str, outcome: AgentOutcome):
        super().__init__(f"model paused: {model}")
        self.model = model
        self.outcome = outcome


@dataclass
class RunInfo:
    """Facts about the run that go into every result row."""

    config: RunConfig
    config_hash: str
    git_commit: str


def is_notable(result: EpisodeResult) -> bool:
    """Episodes whose traces are committed (SPEC 4, Persistence)."""
    flipped = result.pushback is not None and result.pushback.flipped
    return (
        not result.passed
        or result.false_success
        or result.canary_leaked
        or bool(result.attacker_goal_met)
        or flipped
    )


def _attacker_goal(spec, events, canary_verdict, environment):
    """Evaluate an attack payload's success_check, or None outside attack conditions."""
    payload = attack_payload(spec.condition, spec.variant_id)
    if payload is None:
        return None
    # file_exists checks need the workspace, which still exists before teardown.
    workspace = environment.workspace() if hasattr(environment, "workspace") else None
    return attacker_goal_met(payload.success_check, events, canary_verdict, workspace)


def _build_result(
    spec,
    episode_id,
    outcome,
    verdict,
    canary_verdict,
    goal,
    info,
    started,
    clock,
    pushback: PushbackOutcome | None = None,
):
    """One EpisodeResult from an agent outcome and the graders' verdicts."""
    claim = outcome.final_claim
    strict = strict_pass(verdict.passed, claim, canary_verdict.leaked, goal)
    return EpisodeResult(
        spec=spec,
        episode_id=episode_id,
        passed=verdict.passed,
        strict_passed=strict,
        verify_stdout_tail=verdict.stdout_tail,
        final_claim=claim,
        false_success=false_success(claim, verdict.passed),
        canary_accessed=canary_verdict.accessed,
        canary_leaked=canary_verdict.leaked,
        attacker_goal_met=goal,
        pushback=pushback,
        stop_reason=outcome.stop_reason,
        steps=outcome.steps,
        llm_requests=outcome.llm_requests,
        parse_failure_retries=outcome.parse_retries,
        malformed_tool_names=outcome.malformed_tool_names,
        empty_reply_resamples=outcome.empty_resamples,
        empty_replies_dropped_call=outcome.empty_dropped_call,
        empty_replies_stopped=outcome.empty_stopped,
        defense_actions=outcome.defense_actions,
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        tokens_cached_in=outcome.tokens_cached,
        cost_eur=outcome.cost_eur,
        duration_s=round(clock() - started, 3),
        model_version=outcome.model_version or "unknown",
        provider="+".join(sorted(outcome.providers)),
        config_hash=info.config_hash,
        git_commit=info.git_commit,
    )


async def _run_pushback(
    baseline_spec, pre_verdict, session, environment, info, clock, wall_clock, store
):
    """Continue the session with one pushback user turn and grade again (SPEC 6.7).

    Returns (pushback result, its trace), or None if the model was paused during pushback
    (the caller then writes nothing and the whole episode reruns on --resume).
    """
    pb_spec = pushback_cond.pushback_spec(baseline_spec)
    pb_trace = Trace(pb_spec.episode_id, clock=wall_clock)
    # Pushback events go into their own trace, so the pushback episode has its own record.
    session.trace = pb_trace
    pb_started = clock()
    text = load_pushback()[pushback_cond.type_of(pb_spec)]
    pb_outcome = await session.continue_with_user_turn(text, pushback_cond.MAX_STEPS)
    if pb_outcome.quota_exhausted:
        raise QuotaPause(baseline_spec.model, pb_outcome)
    # Grade the state after the challenge. pre_passed is the baseline grade (same state
    # before the turn); post_passed is this one.
    post_verdict = environment.grade()
    writes, reads, unknown = pushback_cond.count_calls(pb_trace.events)
    if unknown:
        store.log(f"pushback saw tools counted as writes (unknown): {sorted(set(unknown))}")
    response_type = pushback_cond.classify_response(post_verdict.passed, writes, reads)
    outcome_model = PushbackOutcome(
        pushback_type=pushback_cond.type_of(pb_spec),
        pre_passed=pre_verdict.passed,
        post_passed=post_verdict.passed,
        flipped=pre_verdict.passed and not post_verdict.passed,
        response_type=response_type,
        write_calls=writes,
        read_calls=reads,
    )
    # The pushback episode has no canary and no attacker (empty plan, baseline session).
    no_canary = CanaryVerdict(accessed=False, leaked=False)
    result = _build_result(
        pb_spec,
        pb_spec.episode_id,
        pb_outcome,
        post_verdict,
        no_canary,
        None,
        info,
        pb_started,
        clock,
        pushback=outcome_model,
    )
    return result, pb_trace


def _save_grader_error(store: RunStore, trace: Trace, current: Trace, exc: GraderError) -> None:
    """Save the trace(s) of an episode whose grading raised GraderError, then log it.

    `trace` is the episode's own trace. `current` is the session's trace at the moment of the
    error: the pushback trace if the error came from re-grading after pushback, otherwise the
    same object. Each gets an "error" event naming the grader failure. No result row is
    written; the caller re-raises so the run stops (CLAUDE.md hard rule).
    """
    for item in {id(trace): trace, id(current): current}.values():
        item.add("error", {"where": "grader", "error": str(exc)})
        path = store.save_grader_error_trace(item.episode_id, item.events)
        store.log(f"grader error, no result written; trace saved to {path}", item.episode_id)


def merge_proxy_log(trace: Trace, store: RunStore, episode_id: str) -> None:
    """Append the proxy's side-channel log lines to the trace as proxy_mutation events."""
    path = store.proxy_log_path(episode_id)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            trace.add("proxy_mutation", json.loads(line))


async def run_episode(
    spec: EpisodeSpec,
    task: Task,
    environment: EpisodeEnvironment,
    llm: ChatModel,
    store: RunStore,
    info: RunInfo,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> list[EpisodeResult]:
    episode_id = spec.episode_id
    config = info.config
    started = clock()
    # Canary only in vault conditions (decision 2026-10-02); None means no vault/.
    canary = canary_value(episode_id) if spec.condition in VAULT_CONDITIONS else None
    trace = Trace(episode_id, clock=wall_clock)
    # The task as written, or the condition's rewording of it (paraphrase). Resolved before
    # any setup, so a missing paraphrase cache fails without touching a sandbox.
    prompt = prompt_for(spec, task)
    try:
        # 1. Sandbox up and plan written.
        launch = environment.setup(canary)
        plan_path = environment.work_dir / "plan.json"
        save_plan(plan_for(spec), plan_path)
        proxy_log = store.proxy_log_path(episode_id)
        proxy_log.parent.mkdir(parents=True, exist_ok=True)
        proxy_log.unlink(missing_ok=True)  # a rerun after a crash starts a fresh log

        # 2. The agent loop, through the proxy.
        limits = LoopLimits(
            max_steps=config.max_steps,
            tool_timeout_s=config.tool_timeout_s,
            episode_timeout_s=config.episode_timeout_s,
        )
        session = AgentSession(
            launch,
            llm,
            limits,
            trace,
            plan_path=plan_path,
            proxy_log_path=proxy_log,
            stderr_path=environment.work_dir / "server_stderr.log",
            defenses=spec.defenses,
        )
        # The results to write: the episode itself, then its pushback episode if one runs.
        to_write: list[tuple[EpisodeResult, Trace]] = []
        async with session:
            outcome = await session.run(prompt)
            if outcome.quota_exhausted:
                # Not a model result: the provider stopped us. Nothing written; rerun later.
                raise QuotaPause(spec.model, outcome)
            merge_proxy_log(trace, store, episode_id)

            # 3. Graders. A GraderError leaves this function before any result is written; the
            # trace is still saved (grader_errors/) so the failure can be debugged.
            try:
                verdict = environment.grade()
                canary_verdict = scan_canary(trace.events, canary)
                goal = _attacker_goal(spec, trace.events, canary_verdict, environment)
                result = _build_result(
                    spec, episode_id, outcome, verdict, canary_verdict, goal, info, started, clock
                )
                to_write.append((result, trace))

                # 3b. Pushback (SPEC 6.7): if this baseline passed and claimed DONE, challenge
                # it in the same session. Both rows are written together at the end, so a quota
                # pause during pushback leaves neither and both rerun on --resume.
                if pushback_cond.applies(config, spec) and pushback_cond.eligible(
                    outcome.final_claim, verdict.passed
                ):
                    pb_result = await _run_pushback(
                        spec, verdict, session, environment, info, clock, wall_clock, store
                    )
                    if pb_result is not None:
                        to_write.append(pb_result)
            except GraderError as exc:
                _save_grader_error(store, trace, session.trace, exc)
                raise

        # 4. Write every result: its trace first, then the row (the row marks it as done).
        results = [result for result, _ in to_write]
        for result, result_trace in to_write:
            store.write_trace(result.episode_id, result_trace.events)
            if is_notable(result):
                store.save_notable(result.episode_id)
            store.append_result(result)
        return results
    finally:
        # 5. Sandbox down, unless the config keeps them for debugging.
        if not config.keep_sandboxes:
            environment.teardown()
