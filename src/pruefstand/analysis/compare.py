"""Paired comparison of two runs: the same episodes, with and without a change (SPEC 9, 11).

What: `compare_runs(a, b, seed)` pairs the result rows of run A and run B by
(model, task_id, condition, variant_id, attempt), and for every condition (and every attack
payload) reports both rates with task-bootstrap intervals, the paired change with its own
interval, the discordant pair counts, an odds ratio and McNemar's test. `comparison_text`
prints it as a table; report/compare_html.py renders `compare.html`.
Why: a defense run repeats exactly the episodes of an earlier run with one thing changed.
Pairing each episode with its twin removes the task-to-task variation that unpaired rates
carry, and McNemar's test uses only the pairs where the outcome changed. The outcome compared
is attack success for poison and inject (did the defense stop the attacker?) and state pass
for every other condition (did the defense cost the agent its work?).
How: `pruefstand compare RUN_A RUN_B` loads both results.jsonl files, calls `compare_runs`,
prints the table and writes RUN_B/compare.html. Only models present in both runs are
compared. Episodes that ended in `transport_failure` on either side are host results, not
model results (SPEC 5.2), so their pairs are dropped and counted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from statsmodels.stats.contingency_tables import mcnemar

from pruefstand.analysis.metrics import is_attack_success
from pruefstand.analysis.stats import Estimate, bootstrap_mean
from pruefstand.models import Condition, EpisodeResult

# McNemar's exact (binomial) test below this many discordant pairs, chi-square above (SPEC 9).
EXACT_BELOW = 25
# Conditions whose outcome is attack success rather than state pass.
ATTACK_CONDITIONS = {Condition.POISON, Condition.INJECT, Condition.RUGPULL}


def pair_key(result: EpisodeResult) -> tuple[str, str, str, str, int]:
    """The key that makes an episode of run A the twin of one in run B."""
    spec = result.spec
    return (spec.model, spec.task_id, spec.condition.value, spec.variant_id, spec.attempt)


def outcome(result: EpisodeResult) -> bool:
    """The compared outcome: attack success for attack conditions, state pass otherwise."""
    if result.spec.condition in ATTACK_CONDITIONS:
        return is_attack_success(result)
    return result.passed


def outcome_name(condition: str) -> str:
    """How the outcome of a condition reads in a table."""
    return "attack success" if Condition(condition) in ATTACK_CONDITIONS else "state pass"


@dataclass
class McNemarResult:
    """The 2x2 table of paired outcomes and McNemar's test on it."""

    both: int  # outcome true in A and in B
    only_a: int  # true in A, false in B (for attacks: the defense stopped the attacker)
    only_b: int  # false in A, true in B
    neither: int  # false in both
    odds_ratio: float  # only_b / only_a, with 0.5 added to both when either is zero
    p_value: float
    exact: bool  # True: exact binomial test; False: chi-square with continuity correction

    @property
    def discordant(self) -> int:
        return self.only_a + self.only_b


def mcnemar_test(pairs: list[tuple[bool, bool]]) -> McNemarResult:
    """McNemar's test on (outcome in A, outcome in B) pairs."""
    both = sum(1 for a, b in pairs if a and b)
    only_a = sum(1 for a, b in pairs if a and not b)
    only_b = sum(1 for a, b in pairs if b and not a)
    neither = sum(1 for a, b in pairs if not a and not b)
    discordant = only_a + only_b
    exact = discordant < EXACT_BELOW
    if discordant == 0:
        # No pair changed: no evidence of a difference at all.
        p_value = 1.0
    else:
        # statsmodels reads the discordant cells from the off-diagonal of [[a, b], [c, d]].
        table = [[both, only_a], [only_b, neither]]
        p_value = float(mcnemar(table, exact=exact, correction=True).pvalue)
    # Haldane's correction keeps the ratio finite when one discordant cell is empty.
    if only_a == 0 or only_b == 0:
        odds_ratio = (only_b + 0.5) / (only_a + 0.5)
    else:
        odds_ratio = only_b / only_a
    return McNemarResult(both, only_a, only_b, neither, odds_ratio, p_value, exact)


@dataclass
class ComparisonRow:
    """One line of the comparison: a condition (or one payload of it) for one model."""

    model: str
    condition: str
    variant: str  # "all" for the whole condition, or one variant id
    n_pairs: int
    rate_a: Estimate
    rate_b: Estimate
    change: Estimate  # rate B minus rate A, per task, bootstrapped
    test: McNemarResult

    @property
    def outcome(self) -> str:
        return outcome_name(self.condition)


@dataclass
class Comparison:
    """Everything `compare` reports."""

    run_a: str
    run_b: str
    models: list[str]
    rows: list[ComparisonRow] = field(default_factory=list)
    unpaired_a: int = 0  # rows of A with no twin in B (other models, conditions, missing)
    unpaired_b: int = 0
    dropped_transport: int = 0  # pairs dropped because either side was a transport failure
    defenses_a: list[str] = field(default_factory=list)
    defenses_b: list[str] = field(default_factory=list)


def _per_task(pairs: list[tuple[str, bool, bool]], which: str) -> dict[str, float]:
    """Per-task mean of A's outcome, B's outcome, or B minus A, over paired episodes."""
    values: dict[str, list[float]] = defaultdict(list)
    for task, a, b in pairs:
        if which == "a":
            values[task].append(float(a))
        elif which == "b":
            values[task].append(float(b))
        else:
            values[task].append(float(b) - float(a))
    return {task: float(np.mean(v)) for task, v in sorted(values.items())}


def _row(model, condition, variant, pairs, seed) -> ComparisonRow:
    """Rates, change and McNemar for one group of (task, outcome A, outcome B) pairs."""
    return ComparisonRow(
        model=model,
        condition=condition,
        variant=variant,
        n_pairs=len(pairs),
        rate_a=bootstrap_mean(_per_task(pairs, "a"), seed),
        rate_b=bootstrap_mean(_per_task(pairs, "b"), seed),
        change=bootstrap_mean(_per_task(pairs, "change"), seed),
        test=mcnemar_test([(a, b) for _, a, b in pairs]),
    )


def compare_runs(
    results_a: list[EpisodeResult],
    results_b: list[EpisodeResult],
    seed: int,
    run_a: str = "A",
    run_b: str = "B",
) -> Comparison:
    """Pair two runs' results and compare every condition and attack payload they share."""
    index_a = {pair_key(r): r for r in results_a}
    index_b = {pair_key(r): r for r in results_b}
    shared = sorted(set(index_a) & set(index_b))
    comparison = Comparison(
        run_a=run_a,
        run_b=run_b,
        models=sorted({key[0] for key in shared}),
        unpaired_a=len(index_a) - len(shared),
        unpaired_b=len(index_b) - len(shared),
        defenses_a=sorted({d for r in results_a for d in r.spec.defenses}),
        defenses_b=sorted({d for r in results_b for d in r.spec.defenses}),
    )
    # Group the pairs by (model, condition) and by (model, condition, variant).
    by_condition: dict[tuple[str, str], list] = defaultdict(list)
    by_variant: dict[tuple[str, str, str], list] = defaultdict(list)
    for key in shared:
        a, b = index_a[key], index_b[key]
        if "transport_failure" in (a.stop_reason, b.stop_reason):
            comparison.dropped_transport += 1
            continue
        model, task, condition, variant, _ = key
        pair = (task, outcome(a), outcome(b))
        by_condition[(model, condition)].append(pair)
        if Condition(condition) in ATTACK_CONDITIONS:
            by_variant[(model, condition, variant)].append(pair)
    # One row per condition, then one per attack payload under it.
    for (model, condition), pairs in sorted(by_condition.items()):
        comparison.rows.append(_row(model, condition, "all", pairs, seed))
        for (v_model, v_condition, variant), v_pairs in sorted(by_variant.items()):
            if (v_model, v_condition) == (model, condition):
                comparison.rows.append(_row(model, condition, variant, v_pairs, seed))
    return comparison


def _pct(estimate: Estimate) -> str:
    """value [low, high] in percent (or points, for a change)."""
    if estimate.n_tasks == 0:
        return "n/a"
    return f"{100 * estimate.value:.1f} [{100 * estimate.low:.1f}, {100 * estimate.high:.1f}]"


def comparison_text(c: Comparison) -> str:
    """The comparison as a terminal table."""
    lines = [
        f"compare A = {c.run_a} (defenses: {', '.join(c.defenses_a) or 'none'})",
        f"        B = {c.run_b} (defenses: {', '.join(c.defenses_b) or 'none'})",
        f"paired by (model, task, condition, variant, attempt); models: {', '.join(c.models)}",
        f"unpaired rows: A {c.unpaired_a}, B {c.unpaired_b}; "
        f"pairs dropped for transport failure: {c.dropped_transport}",
        "rates are % over tasks [95% task bootstrap]; change is B minus A in points;",
        "only A / only B are the discordant pairs; OR = only B / only A; McNemar p "
        f"(exact below {EXACT_BELOW} discordant pairs)",
        "",
    ]
    header = (
        f"{'model':<14} {'condition':<10} {'variant':<26} {'outcome':<15} {'pairs':>5}  "
        f"{'A':<20} {'B':<20} {'change':<22} {'onlyA':>5} {'onlyB':>5} {'OR':>6} {'p':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in c.rows:
        model = row.model.split("/")[-1]
        variant = "" if row.variant == "all" else row.variant
        test = row.test
        lines.append(
            f"{model:<14} {row.condition:<10} {variant:<26} {row.outcome:<15} {row.n_pairs:>5}  "
            f"{_pct(row.rate_a):<20} {_pct(row.rate_b):<20} {_pct(row.change):<22} "
            f"{test.only_a:>5} {test.only_b:>5} {test.odds_ratio:>6.2f} "
            f"{test.p_value:>7.4f}{'' if test.exact else '*'}"
        )
    lines.append("")
    lines.append("* chi-square with continuity correction; otherwise exact binomial.")
    return "\n".join(lines)
