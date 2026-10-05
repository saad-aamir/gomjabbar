"""Tests for runner/grid.py ordering."""

from gomjabbar.runner.grid import interleave_models
from tests.helpers import make_spec


def test_interleave_models_round_robin_in_sort_order():
    specs = [make_spec(model=m, attempt=a) for m in ["b", "a"] for a in [1, 0, 2]]
    order = [(s.model, s.attempt) for s in interleave_models(specs)]
    assert order == [("a", 0), ("b", 0), ("a", 1), ("b", 1), ("a", 2), ("b", 2)]
