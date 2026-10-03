"""Tests for runner/budget.py."""

from pruefstand.runner.budget import BudgetGuard


def test_free_only_never_stops():
    assert BudgetGuard(0, [0.0] * 10).can_start()


def test_stops_before_the_cap():
    guard = BudgetGuard(1.0, [0.3, 0.3])
    assert guard.can_start()  # 0.6 + 0.3 <= 1.0
    guard.add(0.3)
    assert not guard.can_start()  # 0.9 + 0.3 > 1.0
