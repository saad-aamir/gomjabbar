"""Poison condition: the proxy hides instructions in tool descriptions (SPEC 6.4).

What: one episode per file in payloads/poisoning/ that applies to the task's service
(variant "poison-<id>"), 1 attempt, with a plan holding that poison payload.
Why: it measures whether the agent obeys instructions that arrive through a tool's
description rather than from the user (the security dimension). The vault is planted, so a
payload that asks for the secret can actually succeed or fail.
How: expand() turns each payload into a spec; plan_for() rebuilds the payload from the spec's
variant id and hands the proxy a PoisonRule.
"""

from __future__ import annotations

from pruefstand.conditions.baseline import episode_seed
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec, Service
from pruefstand.payloads import attack_payload, attack_variant, load_payloads
from pruefstand.proxy.plan import PoisonRule, ProxyPlan

SERVICE_OF = {"filesystem": Service.FILESYSTEM, "postgres": Service.POSTGRES}


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    # The service is the first path segment of the task id, e.g. "filesystem/standard/...".
    service = SERVICE_OF[task_id.split("/")[0]]
    # One episode per poisoning payload that applies to this service.
    specs = []
    for payload in load_payloads("poisoning", service):
        variant = attack_variant(Condition.POISON, payload)  # e.g. "poison-shadow-audit"
        specs.append(
            EpisodeSpec(
                run_id=run_id,
                task_id=task_id,
                condition=Condition.POISON,
                variant_id=variant,
                model=model,
                attempt=0,
                seed=episode_seed(config.seed, task_id, "poison", payload.id),
                defenses=list(config.defenses),
            )
        )
    return specs


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    # Rebuild the exact payload this episode was created for.
    payload = attack_payload(Condition.POISON, spec.variant_id)
    # Turn the payload into the rule the proxy applies to tools/list responses.
    rule = PoisonRule(
        mode=payload.mode,
        target_tool=payload.target_tool,
        text=payload.text,
        shadow_schema=payload.shadow_schema,
    )
    return ProxyPlan(poisons=[rule])
