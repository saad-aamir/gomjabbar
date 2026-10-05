"""Tests for runner/budget.py."""

from gomjabbar.runner.budget import BudgetGuard


def test_free_only_never_stops():
    assert BudgetGuard(0, [0.0] * 10).can_start()


def test_stops_before_the_cap():
    guard = BudgetGuard(1.0, [0.3, 0.3])
    assert guard.can_start()  # 0.6 + 0.3 <= 1.0
    guard.add(0.3)
    assert not guard.can_start()  # 0.9 + 0.3 > 1.0


def test_key_spend_guard_counts_episodes_in_flight():
    from gomjabbar.runner.budget import KeySpendGuard

    spent = {"usd": 4.40}
    guard = KeySpendGuard(4.50, usage=lambda: spent["usd"])
    assert guard.can_start(avg_episode_usd=0.05, in_flight=2)  # 4.40 + 0.10 <= 4.50
    assert not guard.can_start(avg_episode_usd=0.06, in_flight=2)  # 4.40 + 0.12 > 4.50
    spent["usd"] = 4.49
    assert not guard.can_start(avg_episode_usd=0.02, in_flight=1)


def test_key_spend_guard_fails_closed():
    from gomjabbar.runner.budget import KeySpendGuard

    def broken():
        raise RuntimeError("network down")

    guard = KeySpendGuard(4.50, usage=broken)
    assert not guard.can_start(avg_episode_usd=0.0, in_flight=1)
    assert "network down" in guard.error
