"""Tests for analysis/metrics.py and analysis/stats.py."""

import math

import pytest

from pruefstand.analysis.metrics import (
    incomplete_tasks,
    pass_hat_k,
    per_task_pass_hat_k,
    per_task_pass_rate,
)
from pruefstand.analysis.stats import bootstrap_mean
from tests.helpers import make_result, make_spec


def test_pass_hat_k_hand_computed():
    # 5 attempts, 3 passes, k=2: C(3,2)/C(5,2) = 3/10.
    assert pass_hat_k(5, 3, 2) == pytest.approx(0.3)
    # k=5 with 4 of 5: impossible to pass all 5.
    assert pass_hat_k(5, 4, 5) == 0
    assert pass_hat_k(5, 5, 5) == 1
    # k=1 is the plain pass rate.
    assert pass_hat_k(5, 3, 1) == pytest.approx(0.6)
    # 10 attempts, 7 passes, k=3: C(7,3)/C(10,3) = 35/120.
    assert pass_hat_k(10, 7, 3) == pytest.approx(35 / 120)
    with pytest.raises(ValueError):
        pass_hat_k(3, 1, 5)


def results_for(task, passes):
    return [
        make_result(make_spec(task_id=task, attempt=i), passed=p, strict_passed=p and i == 0)
        for i, p in enumerate(passes)
    ]


def test_per_task_metrics():
    results = results_for("a", [1, 1, 1, 1, 1]) + results_for("b", [1, 0, 1, 1, 0])
    results += results_for("c", [1, 1])
    assert per_task_pass_rate(results) == {"a": 1.0, "b": 0.6, "c": 1.0}
    assert per_task_pass_rate(results, strict=True) == {"a": 0.2, "b": 0.2, "c": 0.5}
    hat = per_task_pass_hat_k(results, 5)
    assert hat == {"a": 1.0, "b": 0.0}
    assert incomplete_tasks(results, 5) == ["c"]


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    per_task = {f"t{i}": v for i, v in enumerate([0, 0.2, 0.4, 1, 1, 0.6, 0.8, 0, 1, 0.5])}
    a = bootstrap_mean(per_task, seed=20261002)
    b = bootstrap_mean(dict(reversed(per_task.items())), seed=20261002)
    assert (a.value, a.low, a.high) == (b.value, b.low, b.high)
    assert a.low <= a.value <= a.high
    assert a.value == pytest.approx(0.55)


def test_bootstrap_of_constant_values_has_zero_width():
    est = bootstrap_mean({"a": 1.0, "b": 1.0}, seed=1)
    assert (est.value, est.low, est.high) == (1.0, 1.0, 1.0)
    assert str(est) == "1.000 [1.000, 1.000]"


def test_bootstrap_empty():
    assert math.isnan(bootstrap_mean({}, seed=1).value)
