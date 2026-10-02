"""The grid runner: expand a config into episodes and run them, resumably.

What: `build_specs` turns a RunConfig into the sorted list of EpisodeSpecs (tasks x models x
conditions x variants x attempts). `run_grid` runs the ones without a result yet, with
bounded concurrency, quota and budget checks, and periodic git checkpoints.
Why: SPEC 5.5. Long free-tier runs stop and resume many times, so the runner must be able to
start from any point and produce the same set of results as an uninterrupted run.
How: the CLI builds the pieces (store, task loader, model clients, quota manager) and calls
`run_grid`. It returns a RunStatus saying whether the grid finished or paused on quota.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from pruefstand.agent.llm import ChatModel
from pruefstand.conditions import expand
from pruefstand.config import RunConfig
from pruefstand.models import Condition, EpisodeSpec, Task
from pruefstand.runner.budget import BudgetGuard
from pruefstand.runner.checkpoint import checkpoint
from pruefstand.runner.environment import EpisodeEnvironment
from pruefstand.runner.episode import QuotaPause, RunInfo, run_episode
from pruefstand.runner.quota import QuotaManager
from pruefstand.runner.store import RunStore


def build_specs(
    config: RunConfig,
    run_id: str,
    task_ids: list[str],
    only: Condition | None = None,
) -> list[EpisodeSpec]:
    """Every EpisodeSpec of the run, in the deterministic order of SPEC 5.5."""
    conditions = [only] if only else list(config.conditions)
    specs = [
        spec
        for model in config.models
        for task_id in task_ids
        for condition in conditions
        # Pushback is derived from baseline episodes (M3), not expanded here.
        if condition != Condition.PUSHBACK
        for spec in expand(condition, config, run_id, task_id, model.name)
    ]
    return sorted(specs, key=lambda s: s.sort_key)


@dataclass
class RunStatus:
    total: int  # specs in the grid
    done: int  # specs with a result (before and during this invocation)
    ran: int = 0  # episodes run by this invocation
    paused_models: set[str] = field(default_factory=set)  # models stopped by quota today
    budget_stop: bool = False

    @property
    def finished(self) -> bool:
        return self.done == self.total


async def run_grid(
    specs: list[EpisodeSpec],
    tasks: dict[str, Task],
    store: RunStore,
    info: RunInfo,
    llm_for: Callable[[str], ChatModel],
    environment_for: Callable[[Task, str], EpisodeEnvironment],
    quota: QuotaManager,
    checkpoint_every: int | None = None,
    on_result: Callable[[str], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> RunStatus:
    """Run every spec that has no result yet. GraderError and other bugs abort the run."""
    completed = store.completed_ids()
    todo = [s for s in specs if s.episode_id not in completed]
    status = RunStatus(total=len(specs), done=len(specs) - len(todo))
    budget = BudgetGuard(info.config.spend_cap_eur, [r.cost_eur for r in store.read_results()])
    store.log(f"run start: {len(specs)} specs, {len(todo)} to do")

    # Deterministic order: a queue in sort order, consumed by `concurrency` workers.
    queue: asyncio.Queue[EpisodeSpec] = asyncio.Queue()
    for spec in todo:
        queue.put_nowait(spec)
    since_checkpoint = 0

    async def worker() -> None:
        nonlocal since_checkpoint
        while True:
            try:
                spec = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            episode_id = spec.episode_id
            # Skip models whose daily quota is used up; their episodes wait for --resume.
            if spec.model in status.paused_models or not quota.can_start(spec.model):
                status.paused_models.add(spec.model)
                continue
            if not budget.can_start():
                status.budget_stop = True
                return
            store.log(
                f"start {spec.model} {spec.task_id} {spec.condition.value} "
                f"{spec.variant_id} #{spec.attempt}",
                episode_id,
            )
            try:
                result = await run_episode(
                    spec,
                    tasks[spec.task_id],
                    environment_for(tasks[spec.task_id], episode_id),
                    llm_for(spec.model),
                    store,
                    info,
                    # Tests pass a fake clock so durations, and so results.jsonl, are identical.
                    **({"clock": clock, "wall_clock": clock} if clock else {}),
                )
            except QuotaPause:
                store.log(
                    f"daily quota exhausted for {spec.model}; episode not written", episode_id
                )
                quota.mark_exhausted(spec.model)
                status.paused_models.add(spec.model)
                continue
            quota.count_episode(spec.model)
            budget.add(result.cost_eur)
            status.done += 1
            status.ran += 1
            since_checkpoint += 1
            line = (
                f"done passed={result.passed} claim={result.final_claim} "
                f"stop={result.stop_reason} steps={result.steps} {result.duration_s}s"
            )
            store.log(line, episode_id)
            if on_result:
                on_result(
                    f"[{status.done}/{status.total}] {spec.model} {spec.task_id} "
                    f"#{spec.attempt}: {line}"
                )
            if checkpoint_every and since_checkpoint >= checkpoint_every:
                since_checkpoint = 0
                checkpoint(store, status.done, status.total)

    workers = [asyncio.create_task(worker()) for _ in range(max(1, info.config.concurrency))]
    try:
        # gather re-raises the first error (for example GraderError) after it happens.
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        store.log("run aborted by an error")
        raise
    finally:
        if checkpoint_every:
            checkpoint(store, status.done, status.total)
    store.log(f"run stop: {status.done}/{status.total} done, paused={sorted(status.paused_models)}")
    return status
