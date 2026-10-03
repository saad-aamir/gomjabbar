"""Integration tests for the grid runner: resume, grader errors and quota pauses.

Episodes run against the fake notes server through the real proxy, with the scripted LLM.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

from pruefstand.agent.llm import QuotaExhausted
from pruefstand.agent.loop import ServerLaunch
from pruefstand.config import RunConfig
from pruefstand.graders.state import GraderError, StateVerdict
from pruefstand.models import Service, Task
from pruefstand.runner.episode import RunInfo
from pruefstand.runner.grid import build_specs, run_grid
from pruefstand.runner.quota import QuotaManager
from pruefstand.runner.store import RunStore
from tests.fixtures.scripted_llm import ScriptedLLM, call, fail, final

FAKE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_server.py"
TASK_IDS = ["filesystem/easy/fake/t1", "filesystem/easy/fake/t2"]


class SimulatedKill(BaseException):
    """Stands in for the process being killed (BaseException, like KeyboardInterrupt)."""


class FakeNotesEnvironment:
    """An EpisodeEnvironment around the fake server: pass if note 'a' says 'hello'."""

    def __init__(self, root: Path, episode_id: str, grader_error: bool = False):
        self.work_dir = root / episode_id
        self.notes = self.work_dir / "notes.json"
        self.grader_error = grader_error

    def setup(self, canary):
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.notes.write_text("{}")
        return ServerLaunch(
            command=[sys.executable, str(FAKE_SERVER)],
            env_extra={"FAKE_NOTES_PATH": str(self.notes)},
        )

    def grade(self):
        if self.grader_error:
            raise GraderError("forced grader error")
        notes = json.loads(self.notes.read_text())
        return StateVerdict(passed=notes.get("a") == "hello", stdout_tail=json.dumps(notes))

    def teardown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)


def make_config(**overrides) -> RunConfig:
    data = dict(
        run_name="t",
        suite="unused",
        services=["filesystem"],
        models=[
            {"name": "good", "free_tier": True, "rpd_limit": 1000},
            {"name": "bad", "free_tier": True, "rpd_limit": 1000},
        ],
        seed=7,
        k=2,
        conditions=["baseline"],
        tool_timeout_s=10,
        episode_timeout_s=60,
        concurrency=1,
    )
    data.update(overrides)
    return RunConfig.model_validate(data)


def tasks() -> dict[str, Task]:
    return {
        task_id: Task(
            id=task_id,
            service=Service.FILESYSTEM,
            description="Write note a = hello.",
            source_dir=Path("."),
            meta={},
        )
        for task_id in TASK_IDS
    }


def script_for(model: str) -> ScriptedLLM:
    """The good model solves the task, the bad one writes the wrong text."""
    text = "hello" if model == "good" else "goodbye"
    return ScriptedLLM([call("write_note", name="a", text=text), final("DONE")])


class FakeClock:
    """Advances one second per reading, so durations are identical between runs."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        self.now += 1
        return self.now


async def run(
    tmp_path, run_dir, llm_for=script_for, grader_error=False, quota_clock=None, stop_after=None
):
    config = make_config()
    store = RunStore(run_dir)
    store.write_config(config)
    specs = build_specs(config, store.run_id, TASK_IDS)
    info = RunInfo(config=config, config_hash=config.config_hash(), git_commit="testcommit")
    quota = QuotaManager(store, config.models, **({"clock": quota_clock} if quota_clock else {}))
    seen = []

    def on_result(line):
        seen.append(line)
        if stop_after is not None and len(seen) >= stop_after:
            # Simulates the process being killed right after a result was written.
            raise SimulatedKill

    status = await run_grid(
        specs,
        tasks(),
        store,
        info,
        llm_for,
        lambda task, episode_id: FakeNotesEnvironment(tmp_path / "sb", episode_id, grader_error),
        quota,
        on_result=on_result,
        clock=FakeClock(),
    )
    return status, store


async def test_full_run_results(tmp_path):
    status, store = await run(tmp_path, tmp_path / "runs" / "full")
    assert status.finished and status.done == 8  # 2 models x 2 tasks x k=2
    results = store.read_results()
    assert all(r.passed for r in results if r.spec.model == "good")
    assert not any(r.passed for r in results if r.spec.model == "bad")
    # The bad model claimed DONE on a failed state: false success.
    assert all(r.false_success for r in results if r.spec.model == "bad")
    # Every row carries config hash, git commit and model version.
    assert all(r.config_hash and r.git_commit == "testcommit" and r.model_version for r in results)
    # Provider and cached tokens are recorded on every row (OpenRouter switch, 2026-10-03).
    assert all(r.provider == "ScriptedCo" for r in results)
    assert all(r.tokens_cached_in == 4 * r.steps for r in results)
    # Failed episodes have their traces saved as notable.
    assert len(list((store.run_dir / "notable").iterdir())) == 4


async def test_kill_and_resume_gives_identical_results(tmp_path):
    _, reference = await run(tmp_path, tmp_path / "ref" / "run")
    run_dir = tmp_path / "resumed" / "run"
    with pytest.raises(SimulatedKill):
        await run(tmp_path, run_dir, stop_after=3)
    assert len(RunStore(run_dir).read_results()) == 3
    # Resume: the 3 completed episodes are skipped, the other 5 run.
    status, store = await run(tmp_path, run_dir)
    assert status.ran == 5 and status.finished
    assert store.results_path.read_bytes() == reference.results_path.read_bytes()


async def test_grader_error_aborts_the_write(tmp_path):
    run_dir = tmp_path / "runs" / "err"
    with pytest.raises(GraderError):
        await run(tmp_path, run_dir, grader_error=True)
    # Nothing was written: no partial or default result.
    assert RunStore(run_dir).read_results() == []


async def test_daily_quota_pauses_cleanly_and_resume_continues(tmp_path):
    _, reference = await run(tmp_path, tmp_path / "ref" / "run")
    run_dir = tmp_path / "quota" / "run"
    calls = {"n": 0}

    def exhausting(model):
        # The 3rd episode's model call hits the provider's daily quota (a daily 429).
        calls["n"] += 1
        if calls["n"] >= 3:
            return ScriptedLLM([fail(QuotaExhausted("requests per day (RPD) exhausted"))])
        return script_for(model)

    day = {"t": 1_790_000_000.0}
    status, store = await run(tmp_path, run_dir, llm_for=exhausting, quota_clock=lambda: day["t"])
    # The run stopped cleanly: no exception, partial results kept, both models paused today.
    assert not status.finished
    assert status.paused_models == {"good", "bad"}
    assert len(store.read_results()) == 2
    quota_state = json.loads(store.quota_path.read_text())
    assert quota_state["good"]["exhausted"] and quota_state["bad"]["exhausted"]

    # Same day: resuming does nothing.
    status, _ = await run(tmp_path, run_dir, quota_clock=lambda: day["t"])
    assert status.ran == 0

    # Next day: resume continues and finishes with the same results as an uninterrupted run.
    day["t"] += 86_400
    status, store = await run(tmp_path, run_dir, quota_clock=lambda: day["t"])
    assert status.finished
    assert store.results_path.read_bytes() == reference.results_path.read_bytes()
