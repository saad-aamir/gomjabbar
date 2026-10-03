"""Plain-text summary of a run: the M1 terminal report card.

What: per model, pass@1 and pass^k with 95% task-level bootstrap intervals, strict pass@1,
the same two under the "MCPMark rule" (episodes that needed an empty-reply re-sample count as
failed), false-success rate, the empty-reply rate, stop reasons, average effort per episode, provider parse-failure rate,
malformed tool names and the serving providers.
Why: M1's gate asks for these numbers in the terminal; the HTML report comes in M3.
How: `pruefstand report RUN_DIR --text` and the end of `run` and `pilot` call `summary_text`.
"""

from __future__ import annotations

from collections import Counter

from pruefstand.analysis.metrics import (
    empty_reply_rate,
    false_success_rate,
    incomplete_tasks,
    model_results,
    per_task_pass_hat_k,
    per_task_pass_rate,
    under_mcpmark_rule,
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
        # Sensitivity: MCPMark's agent ends a task at the first empty reply, so an episode
        # that needed a re-sample counts as failed here.
        strict_rows = under_mcpmark_rule(rows)
        lines.append(
            f"   MCPMark rule      pass@1 {bootstrap_mean(per_task_pass_rate(strict_rows), seed)}"
        )
        if k > 1:
            lines.append(
                f"   MCPMark rule      pass^{k} "
                f"{bootstrap_mean(per_task_pass_hat_k(strict_rows, k), seed)}"
            )
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
        # Provider parse failures: the share of model requests the provider could not parse
        # and that were retried (capped at 3 per call; beyond that the episode is llm_error).
        requests = sum(r.llm_requests for r in rows)
        parse_retries = sum(r.parse_failure_retries for r in rows)
        parse_eps = sum(1 for r in rows if r.parse_failure_retries)
        rate = parse_retries / requests if requests else 0.0
        lines.append(
            f"   parse failures    {parse_retries} retries / {requests} requests = {rate:.3f}"
            f", in {parse_eps}/{n} episodes"
        )
        # Empty replies (no text, no tool call): re-sampled up to 3 times per step.
        empties, replies = empty_reply_rate(rows)
        dropped = sum(r.empty_replies_dropped_call for r in rows)
        stopped = sum(r.empty_replies_stopped for r in rows)
        resamples = sum(r.empty_reply_resamples for r in rows)
        resampled_eps = sum(1 for r in rows if r.empty_reply_resamples)
        lines.append(
            f"   empty replies     {empties} / {replies} replies = "
            f"{empties / replies if replies else 0.0:.3f} "
            f"(dropped call {dropped}, stopped after reasoning {stopped}); "
            f"{resamples} re-samples in {resampled_eps}/{n} episodes"
        )
        # Tool names with a leaked Harmony token, such as "write_file<|channel|>commentary".
        malformed = sum(r.malformed_tool_names for r in rows)
        malformed_eps = sum(1 for r in rows if r.malformed_tool_names)
        lines.append(
            f"   malformed names   {malformed} calls ({malformed / n:.2f} per episode)"
            f", in {malformed_eps}/{n} episodes"
        )
        providers = sorted({r.provider for r in rows if r.provider})
        if providers:
            lines.append(f"   providers         {', '.join(providers)}")
        versions = sorted({r.model_version for r in rows})
        lines.append(f"   model versions    {', '.join(versions)}")
    return "\n".join(lines)
