"""A full campaign with a fake attacker and fake runs, no paid calls."""


from gomjabbar.paths import REPO_ROOT
from gomjabbar.redteam import campaign as campaign_mod
from gomjabbar.redteam.campaign import run_campaign

HUNTER_CONFIG = REPO_ROOT / "configs" / "hunter.yaml"
RUNS_DIR = REPO_ROOT / "runs"


def _patch_runner(monkeypatch, win_on_round: int | None):
    """Make run_one_attack return a win only on the given round index (None = never win)."""
    state = {"calls": 0}

    def fake_run_one_attack(base_config, mode, payload_text, n_tasks, runner=None):
        this_call = state["calls"]
        state["calls"] += 1
        won = n_tasks if this_call == win_on_round else 0
        return won, n_tasks

    monkeypatch.setattr(campaign_mod, "run_one_attack", fake_run_one_attack)


def _fake_attacker(goal, mode, techniques, history):
    """A stand-in attacker: returns a trivial payload and ignores feedback."""
    return f"id: fake-{len(history)}\nmode: append_description\n"


def test_campaign_stops_on_the_winning_round(monkeypatch):
    _patch_runner(monkeypatch, win_on_round=1)   # win on the second attempt
    result = run_campaign("leak the key", "poison", HUNTER_CONFIG, _fake_attacker, max_rounds=4)
    assert result.won is True
    assert result.rounds_used == 2               # stopped right after the win, didn't do all 4


def test_campaign_gives_up_after_max_rounds(monkeypatch):
    _patch_runner(monkeypatch, win_on_round=None)  # never wins
    result = run_campaign("leak the key", "poison", HUNTER_CONFIG, _fake_attacker, max_rounds=3)
    assert result.won is False
    assert result.rounds_used == 3               # tried the full budget, then stopped
