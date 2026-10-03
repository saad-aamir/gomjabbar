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
from pruefstand.runner.budget import BudgetGuard, KeySpendGuard
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


def interleave_models(specs: list[EpisodeSpec]) -> list[EpisodeSpec]:
    """Round robin over models: m1 s1, m2 s1, m1 s2, m2 s2, ... (each model in sort order)."""
    by_model: dict[str, list[EpisodeSpec]] = {}
    for spec in sorted(specs, key=lambda s: s.sort_key):
        by_model.setdefault(spec.model, []).append(spec)
    queues = [by_model[m] for m in sorted(by_model)]
    order = []
    for i in range(max((len(q) for q in queues), default=0)):
        order.extend(q[i] for q in queues if i < len(q))
    return order


# Smallest cost assumed for one episode by the key spend guard, in USD, so the first
# episodes of a run (no average yet) still leave room. The M1 baseline's most expensive
# episode cost about 0.042 USD.
KEY_RESERVE_MIN_USD = 0.05


def key_spend_allows(guard: KeySpendGuard, budget: BudgetGuard, info: RunInfo) -> bool:
    """True if every worker can run one more average episode under the key's spend limit."""
    rate = info.config.usd_to_eur or 1.0
    average_usd = max(budget.average_cost() / rate, KEY_RESERVE_MIN_USD)
    return guard.can_start(average_usd, in_flight=info.config.concurrency)


@dataclass
class RunStatus:
    total: int  # specs in the grid
    done: int  # specs with a result (before and during this invocation)
    ran: int = 0  # episodes run by this invocation
    paused_models: set[str] = field(default_factory=set)  # models stopped by quota today
    budget_stop: bool = False
    key_spend_stop: str = ""  # why the key spend guard stopped the run, "" if it did not
    account_problem: str = ""  # provider message when credit ran out or the key was rejected

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
    key_guard: KeySpendGuard | None = None,
) -> RunStatus:
    """Run every spec that has no result yet. GraderError and other bugs abort the run."""
    completed = store.completed_ids()
    # Interleave the full list first, then drop finished episodes, so a resumed run continues
    # in exactly the order an uninterrupted run would have used.
    todo = [s for s in interleave_models(specs) if s.episode_id not in completed]
    status = RunStatus(total=len(specs), done=len(specs) - len(todo))
    budget = BudgetGuard(info.config.spend_cap_eur, [r.cost_eur for r in store.read_results()])
    store.log(f"run start: {len(specs)} specs, {len(todo)} to do")

    # Deterministic order: a queue consumed by `concurrency` workers. Models are interleaved
    # (round robin, each model's specs in sort order) so that concurrent workers usually run
    # different models and each uses its own per-model rate limit instead of sharing one.
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
            if key_guard is not None and not key_spend_allows(key_guard, budget, info):
                status.key_spend_stop = (
                    key_guard.error
                    or f"key usage USD {key_guard.last_usage:.4f}, cap USD {key_guard.cap_usd:g}"
                )
                store.log(f"key spend guard stopped the run: {status.key_spend_stop}")
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
            except QuotaPause as pause:
                kind = "daily quota exhausted" if pause.outcome.quota_daily else "account problem"
                store.log(
                    f"{kind} for {spec.model}; episode not written; "
                    f"provider said: {pause.outcome.error}",
                    episode_id,
                )
                # Only a daily quota is remembered in quota.json; missing credit or a rejected
                # key pauses this invocation, and --resume works once it is fixed.
                if pause.outcome.quota_daily:
                    quota.mark_exhausted(spec.model)
                else:
                    status.account_problem = pause.outcome.error
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
