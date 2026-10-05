"""Attack payloads and pushback texts, loaded from the YAML files in payloads/.

What: `Payload` (one poisoning or injection file, SPEC 7) with its `SuccessCheck`,
`load_payloads(kind, service)` for the files of one folder, `load_pushback()` for the four
pushback texts, and `pushback_types()` for their fixed order.
Why: the poison and inject conditions build one episode per payload file, and the policy
grader needs each payload's success check. Validating the files once, with clear errors,
means a typo in a payload fails before any episode runs, not halfway through a paid run.
How: conditions/poison.py and conditions/inject.py call `load_payloads` to expand episodes
and build proxy plans; runner/episode.py calls `payload_for_variant` to find the success
check of an attack episode; conditions/pushback.py calls `load_pushback`.

Holdout payloads (M4): payloads/holdout/ holds payloads written after the defense
patterns were frozen (DEVIATIONS.md, 2026-10-05, for who wrote them), so they test the defenses fairly. One folder holds both kinds: a file
with a `mode` is a poisoning payload, one without is an injection payload. A run uses them
instead of the standard folders when its config says `payload_set: holdout`; their variant
ids carry "holdout-" (poison-holdout-<id>, inject-holdout-<id>).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from gomjabbar.models import Condition, Service
from gomjabbar.paths import REPO_ROOT

# Where the payload files live (SPEC 3). poisoning/ and injection/ are loaded, and holdout/
# when a run asks for it; files in other folders (extra/, drafts/) are not.
PAYLOAD_ROOT = REPO_ROOT / "payloads"
# Folder of the held-out payloads, and the marker their variant ids carry.
HOLDOUT_FOLDER = "holdout"
HOLDOUT_TAG = "holdout-"
# Folder name for each attack kind.
PayloadKind = Literal["poisoning", "injection"]
# The four pushback types, in the order of payloads/pushback.yaml; md5 % 4 indexes this.
PUSHBACK_TYPES = ("simple", "social", "emotional", "authoritative")


class PayloadError(Exception):
    """A payload file is missing, malformed or inconsistent."""


class SuccessCheck(BaseModel):
    """How the policy grader decides that the attacker won (SPEC 7)."""

    kind: Literal["canary_leaked", "tool_called", "file_exists", "sql_matches"]
    tool: str | None = None  # tool_called: the tool the attacker wants called
    arg_regex: str | None = None  # tool_called: optional regex over the call's arguments
    path: str | None = None  # file_exists: path relative to the workspace
    regex: str | None = None  # sql_matches: regex over SQL in tool-call arguments

    @model_validator(mode="after")
    def _fields_for_kind(self) -> SuccessCheck:
        # Each kind needs its own field; a missing one would silently never match.
        required = {"tool_called": "tool", "file_exists": "path", "sql_matches": "regex"}
        field = required.get(self.kind)
        if field and not getattr(self, field):
            raise ValueError(f"success_check kind {self.kind!r} needs {field!r}")
        return self


class Payload(BaseModel):
    """One attack payload file. Injection files leave the poisoning-only fields unset."""

    id: str  # unique id, used as the variant_id suffix
    kind: PayloadKind  # which folder it came from
    mode: Literal["append_description", "append_schema", "shadow_tool"] | None = None
    target_tool: str | None = None  # "auto", a real tool, or the shadow tool's name
    rugpull_ok: bool = False  # may the rug pull condition use it (P1)
    services: list[Service] = Field(default_factory=lambda: list(Service))
    text: str
    shadow_schema: dict | None = None
    success_check: SuccessCheck
    holdout: bool = False  # True for files from payloads/holdout/

    @model_validator(mode="after")
    def _poisoning_fields(self) -> Payload:
        # A poisoning payload must say how and where it poisons; injection ones must not.
        if self.kind == "poisoning" and (self.mode is None or self.target_tool is None):
            raise ValueError("poisoning payloads need mode and target_tool")
        if self.kind == "injection" and self.mode is not None:
            raise ValueError("injection payloads have no mode")
        return self

    def applies_to(self, service: Service) -> bool:
        return service in self.services


def _read_yaml(path: Path) -> dict:
    """One YAML file as a dict, with the file name in any error."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PayloadError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise PayloadError(f"{path}: expected a mapping")
    return data


@cache
def _load_folder(kind: PayloadKind, root: Path) -> tuple[Payload, ...]:
    """Every payload of one folder, sorted by id, checked for unique ids. Cached per process."""
    folder = root / kind
    payloads = []
    for path in sorted(folder.glob("*.yaml")):
        try:
            payloads.append(Payload(kind=kind, **_read_yaml(path)))
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            raise PayloadError(f"{path}: {exc}") from exc
    _check_ids(folder, payloads)
    # A standard id starting with "holdout-" would make its variant id ambiguous.
    for payload in payloads:
        if payload.id.startswith(HOLDOUT_TAG):
            raise PayloadError(f"{folder}: id {payload.id!r} must not start with {HOLDOUT_TAG!r}")
    return tuple(sorted(payloads, key=lambda p: p.id))


@cache
def _load_holdout(root: Path) -> tuple[Payload, ...]:
    """Every held-out payload, both kinds: a file with a mode is poisoning, else injection."""
    folder = root / HOLDOUT_FOLDER
    payloads = []
    for path in sorted(folder.glob("*.yaml")):
        data = _read_yaml(path)
        kind: PayloadKind = "poisoning" if data.get("mode") is not None else "injection"
        try:
            payloads.append(Payload(kind=kind, holdout=True, **data))
        except ValueError as exc:
            raise PayloadError(f"{path}: {exc}") from exc
    # Ids are unique across the whole folder, so one id names one file whatever its kind.
    _check_ids(folder, payloads)
    return tuple(sorted(payloads, key=lambda p: p.id))


def _check_ids(folder: Path, payloads: list[Payload]) -> None:
    """Refuse duplicate ids in one folder."""
    ids = [p.id for p in payloads]
    if len(ids) != len(set(ids)):
        raise PayloadError(f"{folder}: duplicate payload ids {sorted(ids)}")


def load_payloads(
    kind: PayloadKind,
    service: Service | None = None,
    root: Path = PAYLOAD_ROOT,
    holdout: bool = False,
) -> list[Payload]:
    """The payloads of one kind, optionally only those that apply to a service.

    With `holdout=True` they come from payloads/holdout/ instead of the kind's own folder.
    """
    if holdout:
        payloads = tuple(p for p in _load_holdout(root) if p.kind == kind)
    else:
        payloads = _load_folder(kind, root)
    return [p for p in payloads if service is None or p.applies_to(service)]


def payload_by_id(
    kind: PayloadKind, payload_id: str, root: Path = PAYLOAD_ROOT, holdout: bool = False
) -> Payload:
    """One payload by id; a missing id is a PayloadError (the spec named a deleted file)."""
    for payload in load_payloads(kind, root=root, holdout=holdout):
        if payload.id == payload_id:
            return payload
    folder = root / (HOLDOUT_FOLDER if holdout else kind)
    raise PayloadError(f"no {kind} payload with id {payload_id!r} in {folder}")


def load_pushback(root: Path = PAYLOAD_ROOT) -> dict[str, str]:
    """The four pushback texts by type (SPEC 6.7), checked to be exactly the four types."""
    data = _read_yaml(root / "pushback.yaml")
    if set(data) != set(PUSHBACK_TYPES):
        raise PayloadError(f"pushback.yaml must define exactly {list(PUSHBACK_TYPES)}")
    return {kind: str(data[kind]).strip() for kind in PUSHBACK_TYPES}


# ---- attack variant ids (shared by the attack conditions and the episode runner) --------
# Each attack condition builds variant ids "<prefix><payload id>" and loads from one folder.
# Both live here, not in conditions/, so the runner can map a variant id back to its payload
# (to grade the attacker's goal) without importing a condition module.
ATTACK_PREFIX: dict[Condition, str] = {Condition.POISON: "poison-", Condition.INJECT: "inject-"}
ATTACK_FOLDER: dict[Condition, PayloadKind] = {
    Condition.POISON: "poisoning",
    Condition.INJECT: "injection",
}


def attack_variant(condition: Condition, payload: Payload) -> str:
    """The variant id of an attack episode, e.g. "poison-shadow-audit" or, for a held-out
    payload, "poison-holdout-<id>"."""
    tag = HOLDOUT_TAG if payload.holdout else ""
    return ATTACK_PREFIX[condition] + tag + payload.id


def attack_payload(
    condition: Condition, variant_id: str, root: Path = PAYLOAD_ROOT
) -> Payload | None:
    """The payload an attack episode uses, or None for a condition with no payload (every
    non-attack condition, so the runner can call it for any episode)."""
    if condition not in ATTACK_PREFIX:
        return None
    payload_id = variant_id.removeprefix(ATTACK_PREFIX[condition])
    # "holdout-<id>" names a held-out payload (standard ids may not start with "holdout-").
    if payload_id.startswith(HOLDOUT_TAG):
        return payload_by_id(
            ATTACK_FOLDER[condition], payload_id.removeprefix(HOLDOUT_TAG), root, holdout=True
        )
    return payload_by_id(ATTACK_FOLDER[condition], payload_id, root)
