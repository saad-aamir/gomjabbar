"""The report card: every metric of a run, per model and per service, with 95% intervals.

What: `build_card(results, k, seed)` computes the numbers the HTML report and the terminal
card show, organized by the four dimensions (SPEC 8, 12): reliability, robustness, security
and behavioural stability. Rates are shown in percent and drops in percentage points.
Why: one place computes the numbers, so the HTML and the text card can never disagree.
How: each metric is a per-task value from analysis/metrics.py, turned into a mean with a
task-level bootstrap interval by analysis/stats.py. Scopes are "all" (both services) and
one per service present in the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pruefstand.analysis.metrics import (
    empty_reply_rate,
    fault_recovery_results,
    model_results,
    of_condition,
    per_task_drop,
    per_task_false_success,
    per_task_pass_hat_k,
    per_task_pass_rate,
    under_mcpmark_rule,
)
from pruefstand.analysis.stats import Estimate, bootstrap_mean
from pruefstand.models import EpisodeResult

# Conditions with a pass rate chart row, in report order.
CONDITIONS = ["baseline", "paraphrase", "fault"]


@dataclass
class Row:
    """One metric line of the card."""

    dimension: str  # "Reliability", "Robustness", ...
    metric: str  # label, e.g. "pass@1 (state)"
    estimate: Estimate | None = None  # mean over tasks with interval, as a fraction
    unit: str = "%"  # "%" for rates, "pts" for drops (both shown times 100)
    note: str = ""  # extra text, e.g. counts

    def value_text(self) -> str:
        """value [low, high] in the row's unit, or the note alone."""
        if self.estimate is None or self.estimate.n_tasks == 0:
            return self.note or "no data"
        e = self.estimate
        return f"{100 * e.value:.1f} [{100 * e.low:.1f}, {100 * e.high:.1f}] {self.unit}"

    def detail_text(self) -> str:
        """How many tasks the estimate is over (and the note), "" for note-only rows."""
        if self.estimate is None or self.estimate.n_tasks == 0:
            return ""
        n = self.estimate.n_tasks
        return f"{n} task{'s' if n != 1 else ''}" + (f"; {self.note}" if self.note else "")

    def text(self) -> str:
        """The whole cell as one line, for the terminal card."""
        detail = self.detail_text()
        return self.value_text() + (f", {detail}" if detail else "")


@dataclass
class Card:
    """The card of one model in one scope."""

    model: str
    scope: str  # "all", "filesystem" or "postgres"
    rows: list[Row] = field(default_factory=list)
    # Points for the charts: condition or fault profile -> estimate.
    condition_pass: dict[str, Estimate] = field(default_factory=dict)
    fault_recovery: dict[str, Estimate] = field(default_factory=dict)
    transport_failures: int = 0


def _service(result: EpisodeResult) -> str:
    return result.spec.task_id.split("/")[0]


def build_scope(results: list[EpisodeResult], model: str, scope: str, k: int, seed: int) -> Card:
    """The card of one model, over the results of one scope."""
    card = Card(model=model, scope=scope)
    rows = model_results(results, model)  # transport failures excluded
    card.transport_failures = len(model_results(results, model, False)) - len(rows)
    base = of_condition(rows, "baseline")
    para = of_condition(rows, "paraphrase")
    fault = of_condition(rows, "fault")

    # Reliability (baseline).
    rel = "Reliability"
    card.rows.append(Row(rel, "pass@1 (state)", bootstrap_mean(per_task_pass_rate(base), seed)))
    card.rows.append(
        Row(rel, "pass@1 (strict)", bootstrap_mean(per_task_pass_rate(base, strict=True), seed))
    )
    if k > 1:
        card.rows.append(Row(rel, f"pass^{k}", bootstrap_mean(per_task_pass_hat_k(base, k), seed)))
    mcp = under_mcpmark_rule(base)
    card.rows.append(
        Row(rel, "pass@1, MCPMark rule", bootstrap_mean(per_task_pass_rate(mcp), seed))
    )
    if k > 1:
        card.rows.append(
            Row(rel, f"pass^{k}, MCPMark rule", bootstrap_mean(per_task_pass_hat_k(mcp, k), seed))
        )
    empties, replies = empty_reply_rate(rows)
    card.rows.append(
        Row(
            rel,
            "empty replies (all conditions)",
            note=f"{empties} of {replies} model replies"
            + (f" = {100 * empties / replies:.1f}%" if replies else ""),
        )
    )

    # Robustness (paraphrase and fault against baseline).
    rob = "Robustness"
    if para:
        card.rows.append(
            Row(
                rob, "drop under paraphrase", bootstrap_mean(per_task_drop(base, para), seed), "pts"
            )
        )
    if fault:
        card.rows.append(
            Row(rob, "drop under fault", bootstrap_mean(per_task_drop(base, fault), seed), "pts")
        )
        recovery = fault_recovery_results(fault)
        card.rows.append(
            Row(rob, "fault recovery", bootstrap_mean(per_task_pass_rate(recovery), seed))
        )
        for profile in sorted({r.spec.variant_id.removeprefix("fault-") for r in fault}):
            only = of_condition(rows, "fault", f"fault-{profile}")
            card.rows.append(
                Row(
                    rob,
                    f"drop under fault: {profile}",
                    bootstrap_mean(per_task_drop(base, only), seed),
                    "pts",
                )
            )
            card.fault_recovery[profile] = bootstrap_mean(per_task_pass_rate(only), seed)
    for condition, group in (("baseline", base), ("paraphrase", para), ("fault", fault)):
        if group:
            card.rows.append(
                Row(
                    rob,
                    f"false success: {condition}",
                    bootstrap_mean(per_task_false_success(group), seed),
                )
            )
            card.condition_pass[condition] = bootstrap_mean(per_task_pass_rate(group), seed)
    if card.transport_failures:
        card.rows.append(
            Row(
                rob,
                "transport failures (host, not model)",
                note=f"{card.transport_failures} episodes, excluded from every metric above",
            )
        )

    # Not measured until M3.
    card.rows.append(Row("Security", "attack success, canary access", note="from M3"))
    card.rows.append(Row("Behavioural stability", "reversal rate, response mix", note="from M3"))
    return card


def build_card(results: list[EpisodeResult], k: int, seed: int) -> list[Card]:
    """Cards for every model, in scopes all, then each service present (sorted)."""
    cards = []
    services = sorted({_service(r) for r in results})
    for model in sorted({r.spec.model for r in results}):
        cards.append(build_scope(results, model, "all", k, seed))
        if len(services) > 1:
            for service in services:
                subset = [r for r in results if _service(r) == service]
                cards.append(build_scope(subset, model, service, k, seed))
    return cards


def card_text(cards: list[Card]) -> str:
    """The card as terminal text (`pruefstand report --text`)."""
    lines = []
    for card in cards:
        lines.append(f"== {card.model} [{card.scope}]")
        dimension = ""
        for row in card.rows:
            if row.dimension != dimension:
                dimension = row.dimension
                lines.append(f"   {dimension}")
            lines.append(f"     {row.metric:38s} {row.text()}")
    return "\n".join(lines)
