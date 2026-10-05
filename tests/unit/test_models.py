"""Tests for the data contracts in models.py."""

from gomjabbar.models import VAULT_CONDITIONS, Condition
from tests.helpers import make_result, make_spec


def test_episode_id_is_deterministic_and_ignores_run_id():
    # Same fields give the same id; run_id is not part of it.
    a = make_spec(run_id="one")
    b = make_spec(run_id="two")
    assert a.episode_id == b.episode_id
    assert len(a.episode_id) == 16


def test_episode_id_changes_with_any_other_field():
    base = make_spec()
    assert make_spec(attempt=1).episode_id != base.episode_id
    assert make_spec(model="other").episode_id != base.episode_id
    assert make_spec(condition=Condition.FAULT).episode_id != base.episode_id
    assert make_spec(defenses=["pinning"]).episode_id != base.episode_id


def test_result_round_trips_through_json():
    result = make_result()
    again = type(result).model_validate_json(result.model_dump_json())
    assert again == result


def test_vault_only_in_attack_and_control_conditions():
    # Decision 2026-10-02: baseline matches MCPMark exactly, so no vault there.
    assert Condition.BASELINE not in VAULT_CONDITIONS
    assert Condition.VAULT_CONTROL in VAULT_CONDITIONS
    assert Condition.POISON in VAULT_CONDITIONS
