"""The runner bridge counts attack wins from a run's results, without any paid call."""

import json

from gomjabbar.paths import REPO_ROOT
from gomjabbar.redteam.runner_bridge import run_one_attack

RUNS_DIR = REPO_ROOT / "runs"
HUNTER_CONFIG = REPO_ROOT / "configs" / "hunter.yaml"


def _fake_run_writing(results: list[dict]):
    """Return a fake runner that creates a hunt-round run folder with the given result rows."""

    def runner(tmp_config):
        run_dir = RUNS_DIR / "hunt-round-faketest"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in results) + "\n"
        )

    return runner


def test_counts_wins_and_total(tmp_path):
    # Three fake episodes: two where the attack won, one where it didn't.
    results = [
        {"attacker_goal_met": True, "canary_leaked": True},
        {"attacker_goal_met": False, "canary_leaked": True},   # leaked counts as a win too
        {"attacker_goal_met": False, "canary_leaked": False},  # a loss
    ]
    won, total = run_one_attack(
        HUNTER_CONFIG, mode="poison", payload_text="id: t\n", n_tasks=3,
        runner=_fake_run_writing(results),
    )
    assert total == 3
    assert won == 2        # two episodes had goal_met or leaked

    # Cleanup the fake run folder so it doesn't linger.
    import shutil
    shutil.rmtree(RUNS_DIR / "hunt-round-faketest", ignore_errors=True)
