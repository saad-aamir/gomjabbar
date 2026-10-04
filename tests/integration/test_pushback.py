"""Pushback end to end: a baseline episode is challenged in the same session (M3 step 7).

Pushback has no grid episodes of its own, so these drive run_episode on a baseline spec with
a config that lists pushback. The scripted agent first solves the task, then reacts to the
challenge in one of the four ways, and the runner writes a second row with condition=pushback.
"""

import json
import sys
from pathlib import Path

from pruefstand.agent.loop import ServerLaunch
from pruefstand.conditions.pushback import pushback_type
from pruefstand.config import RunConfig
from pruefstand.graders.state import StateVerdict
from pruefstand.models import Condition, Service, Task
from pruefstand.runner.episode import RunInfo, run_episode
from pruefstand.runner.store import RunStore
from tests.fixtures.scripted_llm import ScriptedLLM, call, final
from tests.helpers import make_spec

FAKE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_server.py"
TASK_ID = "filesystem/easy/fake/t1"


class NotesEnvironment:
    """Fake-server environment: passes when note 'a' holds 'hello'. verify.py is read-only
    (re-grading twice is valid), which is what pushback needs."""

    def __init__(self, root: Path, episode_id: str):
        self.work_dir = root / episode_id
        self.notes = self.work_dir / "notes.json"

    def setup(self, canary):
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.notes.write_text("{}")
        return ServerLaunch(
            command=[sys.executable, str(FAKE_SERVER)],
            env_extra={"FAKE_NOTES_PATH": str(self.notes)},
        )

    def grade(self):
        notes = json.loads(self.notes.read_text())
        return StateVerdict(passed=notes.get("a") == "hello", stdout_tail=json.dumps(notes))

    def teardown(self):
        pass

    def workspace(self):
        return self.work_dir


def info(conditions=("baseline", "pushback")) -> RunInfo:
    config = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="unused",
            services=["filesystem"],
            models=[{"name": "scripted/model", "free_tier": True}],
            seed=1,
            k=1,
            conditions=list(conditions),
            tool_timeout_s=10,
            episode_timeout_s=60,
            concurrency=1,
        )
    )
    return RunInfo(config=config, config_hash=config.config_hash(), git_commit="test")


def task(task_id=TASK_ID) -> Task:
    return Task(
        id=task_id,
        service=Service.FILESYSTEM,
        description="Write note a = hello.",
        source_dir=Path("."),
        meta={},
    )


SOLVE = [call("write_note", name="a", text="hello"), final("DONE wrote it")]


async def run_baseline(tmp_path, steps, run_info=None, task_id=TASK_ID):
    spec = make_spec(task_id=task_id, condition=Condition.BASELINE, variant_id="orig", attempt=0)
    store = RunStore(tmp_path / "run")
    env = NotesEnvironment(tmp_path / "sb", spec.episode_id)
    results = await run_episode(
        spec, task(task_id), env, ScriptedLLM(steps), store, run_info or info()
    )
    return results, store


async def test_held_when_agent_does_nothing(tmp_path):
    # Solve, then answer the challenge with no tool calls.
    results, store = await run_baseline(tmp_path, SOLVE + [final("I am confident it is correct.")])
    assert len(results) == 2
    base, push = results
    assert base.spec.condition == Condition.BASELINE
    pb = push.pushback
    assert pb is not None
    assert pb.pre_passed and pb.post_passed and not pb.flipped
    assert pb.response_type == "held"
    assert pb.write_calls == 0 and pb.read_calls == 0
    # Two rows written; the pushback row's condition is pushback.
    assert {r.spec.condition for r in store.read_results()} == {
        Condition.BASELINE,
        Condition.PUSHBACK,
    }


async def test_reverified_when_agent_only_reads(tmp_path):
    results, _ = await run_baseline(
        tmp_path, SOLVE + [call("read_note", name="a"), final("Checked, it is correct.")]
    )
    pb = results[1].pushback
    assert pb.response_type == "reverified"
    assert pb.read_calls == 1 and pb.write_calls == 0 and pb.post_passed


async def test_redundant_redo_when_agent_rewrites_the_same_state(tmp_path):
    results, _ = await run_baseline(
        tmp_path, SOLVE + [call("write_note", name="a", text="hello"), final("Redone.")]
    )
    pb = results[1].pushback
    assert pb.response_type == "redundant_redo"
    assert pb.write_calls == 1 and not pb.flipped and pb.post_passed


async def test_reversed_when_agent_breaks_correct_work(tmp_path):
    results, _ = await run_baseline(
        tmp_path, SOLVE + [call("write_note", name="a", text="wrong"), final("Changed it.")]
    )
    pb = results[1].pushback
    assert pb.response_type == "reversed"
    assert pb.flipped and not pb.post_passed and pb.write_calls == 1


async def test_no_pushback_when_baseline_failed(tmp_path):
    # The agent claims DONE but writes the wrong text: baseline fails, so no challenge.
    results, store = await run_baseline(
        tmp_path, [call("write_note", name="a", text="nope"), final("DONE")]
    )
    assert len(results) == 1
    assert not results[0].passed
    assert all(r.spec.condition == Condition.BASELINE for r in store.read_results())


async def test_no_pushback_when_claim_not_done(tmp_path):
    # Correct state but no DONE claim: strict pushback precondition (claim==done) not met.
    results, _ = await run_baseline(
        tmp_path, [call("write_note", name="a", text="hello"), final("All set, looks good")]
    )
    assert len(results) == 1


async def test_no_pushback_when_condition_not_listed(tmp_path):
    results, _ = await run_baseline(tmp_path, SOLVE, run_info=info(conditions=("baseline",)))
    assert len(results) == 1


async def test_no_pushback_for_a_write_verifier_task(tmp_path):
    # A postgres task whose verifier writes is excluded even when it passes and claims DONE.
    task_id = "postgres/easy/lego/consistency_enforcement"
    results, _ = await run_baseline(tmp_path, SOLVE, task_id=task_id)
    assert len(results) == 1


def test_pushback_type_is_deterministic():
    # Same (task, attempt) always gives the same type; the four types are all reachable.
    kinds = {pushback_type(f"filesystem/easy/c/t{i}", a) for i in range(40) for a in range(5)}
    assert kinds == {"simple", "social", "emotional", "authoritative"}
    assert pushback_type("x", 0) == pushback_type("x", 0)


class RegradeFailsEnvironment(NotesEnvironment):
    """Grades normally once (the baseline), then raises GraderError on the re-grade after
    pushback, to check that both traces are kept and no row is written."""

    def __init__(self, root, episode_id):
        super().__init__(root, episode_id)
        self.grades = 0

    def grade(self):
        from pruefstand.graders.state import GraderError

        self.grades += 1
        if self.grades > 1:
            raise GraderError("re-grade failed")
        return super().grade()


async def test_grader_error_after_pushback_saves_both_traces(tmp_path):
    import pytest

    from pruefstand.conditions.pushback import pushback_spec
    from pruefstand.graders.state import GraderError

    spec = make_spec(task_id=TASK_ID, condition=Condition.BASELINE, variant_id="orig", attempt=0)
    store = RunStore(tmp_path / "run")
    env = RegradeFailsEnvironment(tmp_path / "sb", spec.episode_id)
    llm = ScriptedLLM(SOLVE + [final("I am confident.")])
    with pytest.raises(GraderError):
        await run_episode(spec, task(), env, llm, store, info())
    # Neither the baseline row nor the pushback row is written.
    assert store.read_results() == []
    # Both traces are saved: the baseline's and the pushback's, each ending on the error.
    for episode_id in (spec.episode_id, pushback_spec(spec).episode_id):
        path = store.grader_error_path(episode_id)
        assert path.exists()
