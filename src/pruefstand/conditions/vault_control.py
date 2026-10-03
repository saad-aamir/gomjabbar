"""Vault-control condition: the canary vault is present, but no attack (SPEC 6, decision in M3).

What: one episode per task (variant "vault"), 1 attempt, with an EMPTY proxy plan.
Why: the attack conditions add a vault/ folder AND an attack. Without this control we could
not tell whether a drop in success came from the extra folder or from the attack itself.
This episode has the folder and no attack, so it isolates the folder's effect.
How: the runner plants the vault for any condition in its vault set; here the proxy does
nothing, so the only difference from baseline is the presence of the vault.
"""

from __future__ import annotations

from pruefstand.conditions.baseline import episode_seed
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec
from pruefstand.proxy.plan import ProxyPlan

VARIANT = "vault"  # single variant: there is nothing to vary


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    # One episode for this task and model. No payloads to loop over, so the list has one item.
    return [
        EpisodeSpec(
            run_id=run_id,
            task_id=task_id,
            condition=Condition.VAULT_CONTROL,
            variant_id=VARIANT,
            model=model,
            attempt=0,
            seed=episode_seed(config.seed, task_id, "vault_control", VARIANT),
            defenses=list(config.defenses),
        )
    ]


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    # No faults, no poison, no injection: the proxy forwards everything untouched.
    return ProxyPlan()
