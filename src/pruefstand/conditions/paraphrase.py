"""Paraphrase condition: the task reworded, same required end state (SPEC 6.2).

What: one episode per cached paraphrase of the task (variant ids "para-1" ... "para-P"),
1 attempt each, empty proxy plan. The prompt is the cached rewording plus MCPMark's suffix.
Why: compared with baseline it shows how much success depends on the exact wording, which
is part of the robustness dimension.
How: `expand` reads which variants exist in cache/paraphrases/ (a dropped variant has no
episode); before any paraphrase was generated it assumes all P, so `run --dry-run` and
`estimate` still work. `prompt_for` returns the text the episode runner sends.
"""

from __future__ import annotations

import json

from pruefstand.conditions.baseline import episode_seed
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec, Task
from pruefstand.proxy.plan import ProxyPlan
from pruefstand.redteam.paraphrase import cache_path, paraphrased_prompt


def variant_ids(config: RunConfig, task_id: str) -> list[str]:
    """The paraphrase variants of a task: the accepted ones in the cache, else all P."""
    path = cache_path(task_id)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [v["variant_id"] for v in data["variants"]]
    return [f"para-{i}" for i in range(1, config.paraphrases + 1)]


def expand(config: RunConfig, run_id: str, task_id: str, model: str) -> list[EpisodeSpec]:
    return [
        EpisodeSpec(
            run_id=run_id,
            task_id=task_id,
            condition=Condition.PARAPHRASE,
            variant_id=variant,
            model=model,
            attempt=0,
            seed=episode_seed(config.seed, task_id, "paraphrase", variant),
            defenses=list(config.defenses),
        )
        for variant in variant_ids(config, task_id)
    ]


def plan_for(spec: EpisodeSpec) -> ProxyPlan:
    # Only the words change; the tools behave normally.
    return ProxyPlan()


def prompt_for(spec: EpisodeSpec, task: Task) -> str:
    # Raises ParaphraseCacheMissing if the cache was never generated or is stale.
    return paraphrased_prompt(task, spec.variant_id)
