"""Fault condition: the proxy breaks one tool call (SPEC 6.3).

What: one episode per profile in the config's `fault_profiles` (variant "fault-<profile>"),
1 attempt each, with a plan holding one FaultRule: any tool (`*`), the 2nd tools/call.
Why: compared with baseline it shows how well the agent recovers when a tool times out,
errors, answers garbage or answers nothing (the robustness dimension). The 2nd call is
chosen because the first call is usually a harmless listing; the 2nd is real work.
How: the proxy applies the rule (proxy/mutators.py). The real server still executes the
faulted call, so the state may already be changed when the agent sees the error.
"""

from __future__ import annotations

from pruefstand.conditions.baseline import episode_seed
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec
from pruefstand.proxy.plan import FaultRule, ProxyPlan

# Variant ids look like "fault-timeout"; the profile is the part after this prefix.
PREFIX = "fault-"
# Which tools/call is broken (SPEC 6.3).
NTH_CALL = 2


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    return [
        EpisodeSpec(
            run_id=run_id,
            task_id=task_id,
            condition=Condition.FAULT,
            variant_id=PREFIX + profile,
            model=model,
            attempt=0,
            seed=episode_seed(config.seed, task_id, "fault", profile),
            defenses=list(config.defenses),
        )
        for profile in config.fault_profiles
    ]


def profile_of(spec: EpisodeSpec) -> str:
    """The fault profile of a fault episode, from its variant id."""
    return spec.variant_id.removeprefix(PREFIX)


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    return ProxyPlan(faults=[FaultRule(profile=profile_of(spec), tool="*", nth_call=NTH_CALL)])
