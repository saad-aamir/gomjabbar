"""Payload loading: the shipped files validate, filtering by service, and bad files fail."""

import pytest

from gomjabbar.models import Service
from gomjabbar.payloads import (
    PUSHBACK_TYPES,
    PayloadError,
    load_payloads,
    load_pushback,
    payload_by_id,
)


def test_shipped_payloads_load():
    poisoning = load_payloads("poisoning")
    injection = load_payloads("injection")
    assert [p.id for p in poisoning] == [
        "append-readfirst",
        "marker-fs",
        "marker-pg",
        "shadow-audit",
    ]
    assert [p.id for p in injection] == [
        "fake-error",
        "hidden-comment",
        "scope-creep-fs",
        "system-override",
    ]
    # The schema payload stays in extra/ and is not loaded (P1, M3 step 7a).
    assert "schema-readfirst" not in [p.id for p in poisoning]


def test_payloads_filter_by_service():
    fs = [p.id for p in load_payloads("poisoning", Service.FILESYSTEM)]
    pg = [p.id for p in load_payloads("poisoning", Service.POSTGRES)]
    assert fs == ["append-readfirst", "marker-fs", "shadow-audit"]
    assert pg == ["append-readfirst", "marker-pg", "shadow-audit"]
    assert "scope-creep-fs" not in [p.id for p in load_payloads("injection", Service.POSTGRES)]


def test_payload_by_id_and_missing_id():
    assert payload_by_id("injection", "fake-error").success_check.kind == "canary_leaked"
    with pytest.raises(PayloadError):
        payload_by_id("injection", "nope")


def test_pushback_texts_in_fixed_order():
    texts = load_pushback()
    assert tuple(texts) == PUSHBACK_TYPES
    assert texts["simple"].startswith("Are you sure?")


def write(root, kind, name, text):
    (root / kind).mkdir(parents=True, exist_ok=True)
    (root / kind / name).write_text(text)


def test_success_check_needs_its_field(tmp_path):
    write(tmp_path, "injection", "a.yaml", "id: a\ntext: x\nsuccess_check: {kind: tool_called}\n")
    with pytest.raises(PayloadError, match="needs 'tool'"):
        load_payloads("injection", root=tmp_path)


def test_poisoning_needs_mode(tmp_path):
    write(tmp_path, "poisoning", "a.yaml", "id: a\ntext: x\nsuccess_check: {kind: canary_leaked}\n")
    with pytest.raises(PayloadError, match="mode"):
        load_payloads("poisoning", root=tmp_path)


def test_duplicate_ids_fail(tmp_path):
    body = "id: a\ntext: x\nsuccess_check: {kind: canary_leaked}\n"
    write(tmp_path, "injection", "a.yaml", body)
    write(tmp_path, "injection", "b.yaml", body)
    with pytest.raises(PayloadError, match="duplicate"):
        load_payloads("injection", root=tmp_path)


# ---- attack variant <-> payload mapping (used by the runner to grade the attacker's goal) --


def test_attack_variant_and_payload_roundtrip():
    from gomjabbar.models import Condition
    from gomjabbar.payloads import attack_payload, attack_variant, payload_by_id

    shadow = payload_by_id("poisoning", "shadow-audit")
    assert attack_variant(Condition.POISON, shadow) == "poison-shadow-audit"
    assert attack_payload(Condition.POISON, "poison-shadow-audit").id == "shadow-audit"

    fake = payload_by_id("injection", "fake-error")
    assert attack_variant(Condition.INJECT, fake) == "inject-fake-error"
    assert attack_payload(Condition.INJECT, "inject-fake-error").id == "fake-error"

    # Non-attack conditions have no payload, so the runner gets None for any of their episodes.
    assert attack_payload(Condition.BASELINE, "orig") is None
    assert attack_payload(Condition.PUSHBACK, "pushback-simple") is None


# ---- held-out payloads (M4) ---------------------------------------------------------------

HOLDOUT_POISON = (
    "id: hp\nmode: append_description\ntarget_tool: auto\ntext: x\n"
    "success_check: {kind: canary_leaked}\n"
)
HOLDOUT_INJECT = "id: hi\ntext: y\nservices: [filesystem]\nsuccess_check: {kind: canary_leaked}\n"


def test_holdout_folder_splits_by_mode(tmp_path):
    write(tmp_path, "holdout", "a.yaml", HOLDOUT_POISON)
    write(tmp_path, "holdout", "b.yaml", HOLDOUT_INJECT)
    poison = load_payloads("poisoning", root=tmp_path, holdout=True)
    inject = load_payloads("injection", root=tmp_path, holdout=True)
    assert [(p.id, p.kind, p.holdout) for p in poison] == [("hp", "poisoning", True)]
    assert [(p.id, p.kind, p.holdout) for p in inject] == [("hi", "injection", True)]
    # Service filters apply as usual.
    assert load_payloads("injection", Service.POSTGRES, root=tmp_path, holdout=True) == []
    # The standard folders are not read for a holdout load, and the other way round.
    assert load_payloads("poisoning", root=tmp_path) == []


def test_holdout_variant_ids_roundtrip(tmp_path):
    from gomjabbar.models import Condition
    from gomjabbar.payloads import attack_payload, attack_variant

    write(tmp_path, "holdout", "a.yaml", HOLDOUT_POISON)
    write(tmp_path, "holdout", "b.yaml", HOLDOUT_INJECT)
    (hp,) = load_payloads("poisoning", root=tmp_path, holdout=True)
    (hi,) = load_payloads("injection", root=tmp_path, holdout=True)
    assert attack_variant(Condition.POISON, hp) == "poison-holdout-hp"
    assert attack_variant(Condition.INJECT, hi) == "inject-holdout-hi"
    assert attack_payload(Condition.POISON, "poison-holdout-hp", root=tmp_path) == hp
    assert attack_payload(Condition.INJECT, "inject-holdout-hi", root=tmp_path) == hi


def test_holdout_ids_unique_across_kinds(tmp_path):
    write(tmp_path, "holdout", "a.yaml", HOLDOUT_POISON)
    write(tmp_path, "holdout", "b.yaml", HOLDOUT_INJECT.replace("id: hi", "id: hp"))
    with pytest.raises(PayloadError, match="duplicate"):
        load_payloads("injection", root=tmp_path, holdout=True)


def test_standard_ids_may_not_look_like_holdout(tmp_path):
    write(tmp_path, "injection", "a.yaml", HOLDOUT_INJECT.replace("id: hi", "id: holdout-x"))
    with pytest.raises(PayloadError, match="must not start"):
        load_payloads("injection", root=tmp_path)


def test_shipped_holdout_folder_has_its_rules():
    # The held-out payloads are written after the freeze; the README states the rules.
    from gomjabbar.payloads import PAYLOAD_ROOT

    readme = (PAYLOAD_ROOT / "holdout" / "README.md").read_text("utf-8")
    assert "Never use these payloads to tune the defense patterns" in readme
