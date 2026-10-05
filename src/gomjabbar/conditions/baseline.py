"""Baseline condition: the task exactly as written, k attempts, no stress (SPEC 6.1).

What: expands one task and model into k EpisodeSpecs with an empty proxy plan.
Why: baseline is the reference every other condition is compared with, and the k repeats
give the reliability metrics pass@1 and pass^k.
How: called through conditions.expand by the grid runner.
"""

from __future__ import annotations

import hashlib

from gomjabbar.config import RunConfig
from gomjabbar.models import Condition, EpisodeSpec
from gomjabbar.proxy.plan import ProxyPlan


def episode_seed(base_seed: int, *parts: object) -> int:
    """A per-episode seed derived from the run seed, stable across processes."""
    text = ":".join([str(base_seed), *map(str, parts)])
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    return [
        EpisodeSpec(
            run_id=run_id,
            task_id=task_id,
            condition=Condition.BASELINE,
            variant_id="orig",
            model=model,
            attempt=attempt,
            seed=episode_seed(config.seed, task_id, "baseline", attempt),
            defenses=list(config.defenses),
        )
        for attempt in range(config.k)
    ]


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    # Baseline is pure passthrough.
    return ProxyPlan()
