"""Inject condition: the proxy appends instructions to a tool RESULT (SPEC 6.5).

What: one episode per file in payloads/injection/ that applies to the task's service
(variant "inject-<id>"), 1 attempt, with a plan holding that inject payload.
Why: like poison, but the attacker's text arrives inside a tool's output instead of its
description, which is how a real indirect prompt injection reaches an agent (security).
How: mirrors poison.py; plan_for() hands the proxy an InjectRule for the 1st matching call.
"""

from __future__ import annotations

from pruefstand.conditions.baseline import episode_seed
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec, Service
from pruefstand.payloads import attack_payload, attack_variant, load_payloads
from pruefstand.proxy.plan import InjectRule, ProxyPlan

SERVICE_OF = {"filesystem": Service.FILESYSTEM, "postgres": Service.POSTGRES}
NTH_CALL = 1  # the payload rides on the first tool result (SPEC 6.5)


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    service = SERVICE_OF[task_id.split("/")[0]]
    specs = []
    # The standard payload folder, or payloads/holdout/ when the config asks for it (M4).
    holdout = config.payload_set == "holdout"
    for payload in load_payloads("injection", service, holdout=holdout):
        variant = attack_variant(Condition.INJECT, payload)  # e.g. "inject-system-override"
        specs.append(
            EpisodeSpec(
                run_id=run_id,
                task_id=task_id,
                condition=Condition.INJECT,
                variant_id=variant,
                model=model,
                attempt=0,
                seed=episode_seed(config.seed, task_id, "inject", payload.id),
                defenses=list(config.defenses),
            )
        )
    return specs


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    payload = attack_payload(Condition.INJECT, spec.variant_id)
    rule = InjectRule(tool="*", nth_call=NTH_CALL, text=payload.text)
    return ProxyPlan(injects=[rule])
