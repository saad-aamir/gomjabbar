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
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pruefstand.models import Service
from pruefstand.paths import REPO_ROOT

# Where the payload files live (SPEC 3). poisoning/ and injection/ are loaded; files in
# other folders (extra/, drafts/) are not.
PAYLOAD_ROOT = REPO_ROOT / "payloads"
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
    ids = [p.id for p in payloads]
    if len(ids) != len(set(ids)):
        raise PayloadError(f"{folder}: duplicate payload ids {sorted(ids)}")
    return tuple(sorted(payloads, key=lambda p: p.id))


def load_payloads(
    kind: PayloadKind, service: Service | None = None, root: Path = PAYLOAD_ROOT
) -> list[Payload]:
    """The payloads of one kind, optionally only those that apply to a service."""
    payloads = _load_folder(kind, root)
    return [p for p in payloads if service is None or p.applies_to(service)]


def payload_by_id(kind: PayloadKind, payload_id: str, root: Path = PAYLOAD_ROOT) -> Payload:
    """One payload by id; a missing id is a PayloadError (the spec named a deleted file)."""
    for payload in _load_folder(kind, root):
        if payload.id == payload_id:
            return payload
    raise PayloadError(f"no {kind} payload with id {payload_id!r} in {root / kind}")


def load_pushback(root: Path = PAYLOAD_ROOT) -> dict[str, str]:
    """The four pushback texts by type (SPEC 6.7), checked to be exactly the four types."""
    data = _read_yaml(root / "pushback.yaml")
    if set(data) != set(PUSHBACK_TYPES):
        raise PayloadError(f"pushback.yaml must define exactly {list(PUSHBACK_TYPES)}")
    return {kind: str(data[kind]).strip() for kind in PUSHBACK_TYPES}
