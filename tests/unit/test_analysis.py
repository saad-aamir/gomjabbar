"""Tests for analysis/metrics.py and analysis/stats.py."""

import math

import pytest

from gomjabbar.analysis.metrics import (
    incomplete_tasks,
    pass_hat_k,
    per_task_pass_hat_k,
    per_task_pass_rate,
)
from gomjabbar.analysis.stats import bootstrap_mean
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


def test_robustness_drop_is_paired_by_task():
    from gomjabbar.analysis.metrics import per_task_drop
    from gomjabbar.models import Condition
    from tests.helpers import make_result, make_spec

    def res(task, condition, passed, attempt=0):
        spec = make_spec(task_id=task, condition=condition, attempt=attempt)
        return make_result(spec, passed=passed)

    baseline = [
        res("t1", Condition.BASELINE, True, 0),
        res("t1", Condition.BASELINE, True, 1),
        res("t2", Condition.BASELINE, True, 0),
        res("t2", Condition.BASELINE, False, 1),
        res("t3", Condition.BASELINE, True, 0),  # no stressed episode: left out
    ]
    stressed = [res("t1", Condition.FAULT, False), res("t2", Condition.FAULT, True)]
    # t1: 1.0 - 0.0 = 1.0; t2: 0.5 - 1.0 = -0.5 (stress happened to help).
    assert per_task_drop(baseline, stressed) == {"t1": 1.0, "t2": -0.5}


def test_fault_recovery_excludes_transport_failures_and_false_success_per_task():
    from gomjabbar.analysis.metrics import fault_recovery_results, per_task_false_success
    from tests.helpers import make_result, make_spec

    ok = make_result(make_spec(task_id="t1"), passed=False, false_success=True)
    dead = make_result(make_spec(task_id="t1", attempt=1), stop_reason="transport_failure")
    assert fault_recovery_results([ok, dead]) == [ok]
    assert per_task_false_success([ok, dead]) == {"t1": 0.5}
