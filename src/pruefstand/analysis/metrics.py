"""Reliability metrics: pass@1 and pass^k, computed per task.

What: groups results by task and computes each task's pass rate and its pass^k estimate.
Why: SPEC 8. pass@1 says how often one attempt succeeds; pass^k says how likely k attempts
in a row all succeed, which is what a user relying on the agent experiences. The gap between
them is hypothesis H1. Values are kept per task because the bootstrap (stats.py) resamples
tasks, not episodes.
How: `per_task(results, ...)` returns {task_id: value}; stats.bootstrap_mean turns that
into a mean with a 95% interval.
"""

from __future__ import annotations

from collections import defaultdict
from math import comb

from pruefstand.models import EpisodeResult


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(all k attempts pass) from n attempts with c passes: C(c,k)/C(n,k)."""
    if not 0 < k <= n:
        raise ValueError(f"need 0 < k <= n, got k={k}, n={n}")
    return comb(c, k) / comb(n, k)


def group_by_task(results: list[EpisodeResult]) -> dict[str, list[EpisodeResult]]:
    """Results grouped by task id, with tasks in sorted order (determinism, SPEC 9)."""
    groups: dict[str, list[EpisodeResult]] = defaultdict(list)
    for result in results:
        groups[result.spec.task_id].append(result)
    return {task: groups[task] for task in sorted(groups)}


def per_task_pass_rate(results: list[EpisodeResult], strict: bool = False) -> dict[str, float]:
    """Each task's share of passing episodes (state pass, or strict pass)."""
    rates = {}
    for task, group in group_by_task(results).items():
        passes = sum(r.strict_passed if strict else r.passed for r in group)
        rates[task] = passes / len(group)
    return rates


def per_task_pass_hat_k(results: list[EpisodeResult], k: int) -> dict[str, float]:
    """Each task's pass^k. Tasks with fewer than k attempts are left out (see `incomplete`)."""
    values = {}
    for task, group in group_by_task(results).items():
        if len(group) >= k:
            values[task] = pass_hat_k(len(group), sum(r.passed for r in group), k)
    return values


def incomplete_tasks(results: list[EpisodeResult], k: int) -> list[str]:
    """Tasks with fewer than k attempts so far (excluded from pass^k)."""
    return [t for t, g in group_by_task(results).items() if len(g) < k]


def false_success_rate(results: list[EpisodeResult]) -> float:
    """Share of episodes where the agent claimed DONE on a failing state."""
    return sum(r.false_success for r in results) / len(results) if results else 0.0


def model_results(
    results: list[EpisodeResult], model: str, exclude_transport_failures: bool = True
) -> list[EpisodeResult]:
    """One model's results. transport_failure episodes are host results, not model results
    (PRE_REGISTRATION.md), so they are excluded from model metrics by default."""
    return [
        r
        for r in results
        if r.spec.model == model
        and not (exclude_transport_failures and r.stop_reason == "transport_failure")
    ]


def under_mcpmark_rule(results: list[EpisodeResult]) -> list[EpisodeResult]:
    """The results as MCPMark's agent would have scored them: any episode that needed an
    empty-reply re-sample counts as failed, because MCPMark ends the task at the first empty
    reply (docs/notes/empty-replies.md). A sensitivity check, not a headline metric."""
    return [
        r.model_copy(update={"passed": False, "strict_passed": False})
        if r.empty_reply_resamples
        else r
        for r in results
    ]


def empty_reply_rate(results: list[EpisodeResult]) -> tuple[int, int]:
    """(empty replies, model replies). Model replies are steps plus re-sampled replies."""
    empties = sum(r.empty_replies_dropped_call + r.empty_replies_stopped for r in results)
    replies = sum(r.steps + r.empty_reply_resamples for r in results)
    return empties, replies
