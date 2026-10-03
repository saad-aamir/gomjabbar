"""Payload loading: the shipped files validate, filtering by service, and bad files fail."""

import pytest

from pruefstand.models import Service
from pruefstand.payloads import (
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
