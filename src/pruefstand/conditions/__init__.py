"""Conditions: each stress condition expands tasks into EpisodeSpecs and proxy plans (SPEC 6).

What: a registry from Condition to the module that implements it.
Why: the grid runner should not know the details of any condition; adding one (M2) means
adding a module and one registry line.
How: `expand(condition, ...)` returns the specs for one task and model; `plan_for(spec)`
returns the ProxyPlan the proxy should apply to that episode.
"""

from __future__ import annotations

from pruefstand.conditions import baseline
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec
from pruefstand.proxy.plan import ProxyPlan

# Conditions implemented so far. M2 adds paraphrase, fault, poison, inject and vault_control.
IMPLEMENTED = {Condition.BASELINE: baseline}


class NotImplementedCondition(Exception):
    """The config asks for a condition that a later milestone implements."""


def module_for(condition: Condition):
    if condition not in IMPLEMENTED:
        raise NotImplementedCondition(
            f"condition {condition.value!r} is not implemented yet; use --only baseline"
        )
    return IMPLEMENTED[condition]


def expand(
    condition: Condition, config: RunConfig, run_id: str, task_id: str, model: str
) -> list[EpisodeSpec]:
    return module_for(condition).expand(config, run_id, task_id, model)


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    return module_for(spec.condition).plan_for(spec)
