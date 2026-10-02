"""Plain-text summary of a run: the M1 terminal report card.

What: per model, pass@1 and pass^k with 95% task-level bootstrap intervals, strict pass@1,
false-success rate, stop reasons and average effort per episode.
Why: M1's gate asks for these numbers in the terminal; the HTML report comes in M3.
How: `pruefstand report RUN_DIR --text` and the end of `run` and `pilot` call `summary_text`.
"""

from __future__ import annotations

from collections import Counter

from pruefstand.analysis.metrics import (
    false_success_rate,
    incomplete_tasks,
    model_results,
    per_task_pass_hat_k,
    per_task_pass_rate,
)
from pruefstand.analysis.stats import bootstrap_mean
from pruefstand.models import Condition, EpisodeResult


def summary_text(results: list[EpisodeResult], k: int, seed: int) -> str:
    """The report card for the baseline episodes of a run."""
    baseline = [r for r in results if r.spec.condition == Condition.BASELINE]
    if not baseline:
        return "No baseline results yet."
    lines = []
    for model in sorted({r.spec.model for r in baseline}):
        rows = model_results(baseline, model)
        transport = len(model_results(baseline, model, exclude_transport_failures=False)) - len(
            rows
        )
        tasks = sorted({r.spec.task_id for r in rows})
        lines.append(f"== {model}")
        lines.append(
            f"   episodes {len(rows)} on {len(tasks)} tasks"
            + (f" (+{transport} transport failures, excluded)" if transport else "")
        )
        if not rows:
            continue
        lines.append(f"   pass@1 (state)    {bootstrap_mean(per_task_pass_rate(rows), seed)}")
        lines.append(
            f"   pass@1 (strict)   {bootstrap_mean(per_task_pass_rate(rows, strict=True), seed)}"
        )
        if k > 1:
            hat = per_task_pass_hat_k(rows, k)
            missing = incomplete_tasks(rows, k)
            note = f"  ({len(missing)} tasks with < {k} attempts left out)" if missing else ""
            lines.append(f"   pass^{k} (state)    {bootstrap_mean(hat, seed)}{note}")
        lines.append(f"   false success     {false_success_rate(rows):.3f}")
        stops = Counter(r.stop_reason for r in rows)
        lines.append(
            "   stop reasons      " + ", ".join(f"{s}={n}" for s, n in sorted(stops.items()))
        )
        n = len(rows)
        lines.append(
            "   per episode       "
            f"steps {sum(r.steps for r in rows) / n:.1f}, "
            f"requests {sum(r.llm_requests for r in rows) / n:.1f}, "
            f"tokens {sum(r.tokens_in + r.tokens_out for r in rows) / n:,.0f}, "
            f"{sum(r.duration_s for r in rows) / n:.0f}s, "
            f"EUR {sum(r.cost_eur for r in rows) / n:.4f}"
        )
        versions = sorted({r.model_version for r in rows})
        lines.append(f"   model versions    {', '.join(versions)}")
    return "\n".join(lines)
