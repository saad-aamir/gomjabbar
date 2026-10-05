"""ProxyPlan: what the chaos proxy should do to the traffic of one episode.

What: Pydantic models for fault, poison, inject and rug pull rules (SPEC 5.2).
Why: the runner decides the stress for an episode and the proxy carries it out in a separate
process, so the plan is the contract between the two. It is written to plan.json.
How: the runner builds a ProxyPlan per EpisodeSpec, saves it with `save_plan`, and passes the
path on the proxy command line; the proxy loads it with `load_plan`. An empty plan means
pure passthrough, which is what baseline uses. Fault mutations live in mutators.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class FaultRule(BaseModel):
    """Break one tools/call response."""

    profile: Literal[
        "latency",
        "timeout",
        "rpc_error",
        "tool_error",
        "malformed",
        "empty",
        "rate_limit",
        "partial",
    ]
    tool: str = "*"  # tool name or "*" for any
    nth_call: int = 2  # fire on the nth matching tools/call (1-based), once
    latency_ms: int = 5000  # only for "latency"


class PoisonRule(BaseModel):
    """Change tool descriptions in tools/list responses."""

    mode: Literal["append_description", "append_schema", "shadow_tool"]  # append_schema is P1
    target_tool: str  # tool whose description gets the payload, or name of the shadow tool
    text: str  # payload text
    shadow_schema: dict | None = None  # input schema for a shadow tool


class InjectRule(BaseModel):
    """Append a text block to one tools/call result."""

    tool: str = "*"  # tool whose result gets the payload
    nth_call: int = 1
    text: str


class RugPullRule(BaseModel):
    """Serve clean descriptions first, then poisoned ones (P1)."""

    after_calls: int = 3  # after this many tools/call in total, switch descriptions
    poison: PoisonRule  # what the descriptions become


class ProxyPlan(BaseModel):
    """Every rule for one episode. All lists empty means passthrough."""

    faults: list[FaultRule] = Field(default_factory=list)
    poisons: list[PoisonRule] = Field(default_factory=list)
    injects: list[InjectRule] = Field(default_factory=list)
    rugpull: RugPullRule | None = None

    def is_empty(self) -> bool:
        """True when the proxy must forward every byte unchanged."""
        return not (self.faults or self.poisons or self.injects or self.rugpull)


def save_plan(plan: ProxyPlan, path: Path) -> None:
    """Write the plan as JSON for the proxy process."""
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def load_plan(path: Path) -> ProxyPlan:
    """Read a plan written by `save_plan`."""
    return ProxyPlan.model_validate_json(path.read_text(encoding="utf-8"))
