"""The exploratory dev numbers behind the hypotheses H1 to H6 in PRE_REGISTRATION.md.

What: reads the committed results of the M2 run (baseline, paraphrase, fault) and the M3 run
(baseline, vault_control, poison, inject, pushback) and prints, for each proposed hypothesis,
the dev-suite estimates it rests on: means over tasks with 95% task-bootstrap intervals, and
the per-task difference the hypothesis is about, also with an interval.
Why: every number in PRE_REGISTRATION.md and the README must come from results.jsonl through
analysis/, never be typed by hand (PRE_REGISTRATION integrity rules). This script is the one
place they come from, so anyone can rerun it and check them.
How: `uv run python scripts/exploratory_numbers.py`. No model is called.
"""

from __future__ import annotations

from collections import defaultdict

from pruefstand.analysis.metrics import (
    model_results,
    of_condition,
    of_pushback_type,
    per_task_attack_success,
    per_task_drop,
    per_task_pass_hat_k,
    per_task_pass_rate,
    per_task_reversal,
    pushback_results,
)
from pruefstand.analysis.stats import Estimate, bootstrap_mean
from pruefstand.paths import REPO_ROOT
from pruefstand.runner.store import RunStore

M2_RUN = REPO_ROOT / "runs" / "dev-20261003-052646"
M3_RUN = REPO_ROOT / "runs" / "dev-20261003-205231"
MODELS = ["openrouter/openai/gpt-oss-120b", "openrouter/openai/gpt-oss-20b"]
SEED = 20261002  # the dev config's seed
PROCEDURAL = ["poison-shadow-audit", "poison-marker-fs", "poison-marker-pg"]


def pct(e: Estimate) -> str:
    """value [low, high] in percent or points, with the number of tasks."""
    return f"{100 * e.value:.1f} [{100 * e.low:.1f}, {100 * e.high:.1f}] (n={e.n_tasks} tasks)"


def diff(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """Per-task a minus b over the tasks both have."""
    return {task: a[task] - b[task] for task in sorted(a) if task in b}


def short(model: str) -> str:
    return model.split("/")[-1]


def main() -> None:
    m2 = RunStore(M2_RUN).read_results()
    m3 = RunStore(M3_RUN).read_results()
    attack_rate: dict[str, dict[str, float]] = {}

    print("H1. pass^5 vs pass@1 (baseline, k=5)")
    for run_name, rows in (("M2", m2), ("M3", m3)):
        for model in MODELS:
            base = of_condition(model_results(rows, model), "baseline")
            p1 = per_task_pass_rate(base)
            p5 = per_task_pass_hat_k(base, 5)
            print(
                f"  {run_name} {short(model)}: pass@1 {pct(bootstrap_mean(p1, SEED))}, "
                f"pass^5 {pct(bootstrap_mean(p5, SEED))}, "
                f"gap {pct(bootstrap_mean(diff(p1, p5), SEED))}"
            )

    print("H2. poison attack success, gpt-oss-120b vs gpt-oss-20b (M3)")
    for model in MODELS:
        poison = of_condition(model_results(m3, model), "poison")
        attack_rate[model] = per_task_attack_success(poison)
        print(f"  {short(model)}: {pct(bootstrap_mean(attack_rate[model], SEED))}")
    gap = diff(attack_rate[MODELS[0]], attack_rate[MODELS[1]])
    print(f"  120b minus 20b, per task: {pct(bootstrap_mean(gap, SEED))}")

    print("H3. procedural payloads vs append-readfirst (M3, poison)")
    for model in MODELS:
        rows = model_results(m3, model)
        for variant in [*PROCEDURAL, "poison-append-readfirst"]:
            per_task = per_task_attack_success(of_condition(rows, "poison", variant))
            print(f"  {short(model)} {variant}: {pct(bootstrap_mean(per_task, SEED))}")
        # Per task: mean success over the procedural payloads that apply, minus append-readfirst.
        procedural = defaultdict(list)
        for variant in PROCEDURAL:
            for task, value in per_task_attack_success(
                of_condition(rows, "poison", variant)
            ).items():
                procedural[task].append(value)
        proc_mean = {task: sum(v) / len(v) for task, v in procedural.items()}
        readfirst = per_task_attack_success(of_condition(rows, "poison", "poison-append-readfirst"))
        print(
            f"  {short(model)} procedural minus append-readfirst, per task: "
            f"{pct(bootstrap_mean(diff(proc_mean, readfirst), SEED))}"
        )

    print("H4. poison vs inject attack success (M3)")
    for model in MODELS:
        rows = model_results(m3, model)
        inject = per_task_attack_success(of_condition(rows, "inject"))
        print(
            f"  {short(model)}: poison {pct(bootstrap_mean(attack_rate[model], SEED))}, "
            f"inject {pct(bootstrap_mean(inject, SEED))}, "
            f"poison minus inject {pct(bootstrap_mean(diff(attack_rate[model], inject), SEED))}"
        )

    print("H5. reversal rate by pushback type (M3)")
    for model in MODELS:
        rows = model_results(m3, model)
        for kind in ("simple", "social", "emotional", "authoritative"):
            typed = of_pushback_type(rows, kind)
            reversed_count = sum(r.pushback.response_type == "reversed" for r in typed)
            print(
                f"  {short(model)} {kind}: {pct(bootstrap_mean(per_task_reversal(typed), SEED))}, "
                f"{reversed_count} of {len(typed)} episodes reversed"
            )
        all_pb = pushback_results(rows)
        print(f"  {short(model)} all types: {pct(bootstrap_mean(per_task_reversal(all_pb), SEED))}")

    print("H6. robustness drop under empty vs tool_error (M2, points)")
    for model in MODELS:
        rows = model_results(m2, model)
        base = of_condition(rows, "baseline")
        drops = {
            profile: per_task_drop(base, of_condition(rows, "fault", f"fault-{profile}"))
            for profile in ("empty", "tool_error")
        }
        print(
            f"  {short(model)}: empty {pct(bootstrap_mean(drops['empty'], SEED))}, "
            f"tool_error {pct(bootstrap_mean(drops['tool_error'], SEED))}, "
            f"empty minus tool_error "
            f"{pct(bootstrap_mean(diff(drops['empty'], drops['tool_error']), SEED))}"
        )


if __name__ == "__main__":
    main()
