"""Tests for runner/store.py: append-only results and resume support."""

import pytest

from pruefstand.config import RunConfig
from pruefstand.runner.store import RunStore
from tests.helpers import make_result, make_spec
from tests.unit.test_config import minimal


def test_append_and_completed_ids(tmp_path):
    store = RunStore(tmp_path / "run1")
    r1 = make_result(make_spec(attempt=0))
    r2 = make_result(make_spec(attempt=1))
    store.append_result(r1)
    store.append_result(r2)
    assert store.completed_ids() == {r1.episode_id, r2.episode_id}
    assert store.read_results() == [r1, r2]


def test_partial_last_line_is_ignored_and_repaired(tmp_path):
    store = RunStore(tmp_path / "run1")
    r1 = make_result(make_spec(attempt=0))
    store.append_result(r1)
    # Simulate a crash in the middle of writing the second row.
    with open(store.results_path, "a") as handle:
        handle.write('{"spec": {"run_id"')
    assert store.completed_ids() == {r1.episode_id}
    r2 = make_result(make_spec(attempt=1))
    store.append_result(r2)
    assert store.read_results() == [r1, r2]


def test_config_mismatch_refused(tmp_path):
    store = RunStore(tmp_path / "run1")
    store.write_config(RunConfig.model_validate(minimal()))
    # Writing the same config again is fine (resume).
    store.write_config(RunConfig.model_validate(minimal()))
    with pytest.raises(ValueError, match="different config"):
        store.write_config(RunConfig.model_validate(minimal(k=2)))
