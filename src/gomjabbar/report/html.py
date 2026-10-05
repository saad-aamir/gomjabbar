"""The HTML report of a run: one self-contained page that opens offline (SPEC 12).

What: `write_report(run_dir)` reads a run's results and config and writes
`runs/<run_id>/report.html` with the report card (section 1), pass rate per condition
(2), fault recovery per fault profile (3), attack success per payload (4) and pushback (4b),
failing episodes with trace links (5) and run metadata (6), including the defenses (M4).
Why: the report is how results are read and shared; it must work without network access,
so styles and charts are inline.
How: report/card.py computes the numbers, report/svg.py draws the charts, and a Jinja2
template in report/templates/ lays out the page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from gomjabbar.analysis.metrics import RESPONSE_TYPES
from gomjabbar.config import load_config
from gomjabbar.paths import REPO_ROOT
from gomjabbar.report.card import CONDITIONS, Card, build_card
from gomjabbar.report.svg import dot_interval_chart
from gomjabbar.runner.store import RunStore

TEMPLATES = Path(__file__).parent / "templates"


def _dimensions(cards: list[Card]) -> list[tuple[str, list[str]]]:
    """Dimensions and metric labels in order of first appearance across the scopes."""
    order: dict[str, list[str]] = {}
    for card in cards:
        for row in card.rows:
            labels = order.setdefault(row.dimension, [])
            if row.metric not in labels:
                labels.append(row.metric)
    return list(order.items())


def _cell(card: Card, label: str) -> tuple[str, str]:
    """(value, detail) of one metric in one scope, ("", "") if the scope lacks it."""
    for row in card.rows:
        if row.metric == label:
            return row.value_text(), row.detail_text()
    return "", ""


def _short(model: str) -> str:
    """openrouter/openai/gpt-oss-120b -> gpt-oss-120b, for chart tooltips."""
    return model.rsplit("/", 1)[-1]


def build_page(run_dir: Path) -> str:
    """The report page as HTML text."""
    store = RunStore(run_dir)
    config = load_config(store.config_path)
    results = store.read_results()
    cards = build_card(results, config.k, config.seed)
    models = sorted({r.spec.model for r in results})
    scopes = []
    for card in cards:
        if card.scope not in scopes:
            scopes.append(card.scope)

    def series(scope: str, attribute: str) -> list[tuple[str, dict]]:
        return [
            (_short(c.model), getattr(c, attribute))
            for model in models
            for c in cards
            if c.model == model and c.scope == scope
        ]

    condition_charts = [
        (
            scope,
            dot_interval_chart(
                [c for c in CONDITIONS if any(c in p for _, p in series(scope, "condition_pass"))],
                series(scope, "condition_pass"),
                f"Pass rate per condition, {scope}",
            ),
        )
        for scope in scopes
    ]
    fault_charts = []
    for scope in scopes:
        profiles = sorted({p for _, pts in series(scope, "fault_recovery") for p in pts})
        if profiles:
            fault_charts.append(
                (
                    scope,
                    dot_interval_chart(
                        profiles, series(scope, "fault_recovery"), f"Fault recovery, {scope}"
                    ),
                )
            )

    # Section 4: attack success per payload (one dot per model, per payload label).
    attack_charts = []
    for scope in scopes:
        labels = sorted({p for _, pts in series(scope, "attack_by_payload") for p in pts})
        if labels:
            attack_charts.append(
                (
                    scope,
                    dot_interval_chart(
                        labels, series(scope, "attack_by_payload"), f"Attack success, {scope}"
                    ),
                )
            )
    # Whether any attack ran, and whether any canary leaked (SPEC: say so plainly if none did).
    attack_episodes = sum(c.attack_episodes for c in cards if c.scope == "all")
    canary_leaks = sum(c.canary_leaks for c in cards if c.scope == "all")
    # Pushback response mix per challenge type, per model (scope "all").
    pushback_tables = [
        (_short(c.model), c.pushback_by_type)
        for m in models
        for c in cards
        if c.model == m and c.scope == "all" and c.pushback_by_type
    ]

    def row(r):
        trace = store.notable_path(r.episode_id)
        return {
            "model": _short(r.spec.model),
            "task": r.spec.task_id,
            "condition": r.spec.condition.value,
            "variant": r.spec.variant_id,
            "attempt": r.spec.attempt,
            "stop": r.stop_reason,
            "claim": r.final_claim,
            "false_success": r.false_success,
            "episode_id": r.episode_id,
            "trace": f"notable/{trace.name}" if trace.exists() else "",
        }

    ordered = sorted(results, key=lambda r: r.spec.sort_key)
    transport = [row(r) for r in ordered if r.stop_reason == "transport_failure"]
    failing = [row(r) for r in ordered if not r.passed and r.stop_reason != "transport_failure"]

    commit_file = REPO_ROOT / "vendor" / "MCPMARK_COMMIT"
    meta_rows = [
        ("run id", store.run_id),
        ("config hash", ", ".join(sorted({r.config_hash for r in results})) or "-"),
        ("git commits", ", ".join(sorted({r.git_commit for r in results})) or "-"),
        ("model versions", ", ".join(sorted({r.model_version for r in results})) or "-"),
        ("providers", ", ".join(sorted({r.provider for r in results if r.provider})) or "-"),
        ("MCPMark commit", commit_file.read_text().strip() if commit_file.exists() else "-"),
        # Host-side defenses (M4) and how often they acted, per condition.
        ("defenses", ", ".join(config.defenses) or "none"),
        ("defense actions", _defense_actions(results)),
        ("temperature", str(config.temperature)),
        ("seed", str(config.seed)),
        ("generated", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
    ]
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2"]))
    template = env.get_template("report.html.j2")
    return template.render(
        meta={
            "run_id": store.run_id,
            "episodes": len(results),
            "models": [_short(m) for m in models],
            "services": sorted({r.spec.task_id.split("/")[0] for r in results}),
            "rows": meta_rows,
        },
        cards_by_model=[(m, [c for c in cards if c.model == m]) for m in models],
        dimensions=_dimensions,
        cell=_cell,
        condition_charts=condition_charts,
        fault_charts=fault_charts,
        attack_charts=attack_charts,
        attack_episodes=attack_episodes,
        canary_leaks=canary_leaks,
        pushback_tables=pushback_tables,
        response_types=RESPONSE_TYPES,
        transport=transport,
        failing=failing,
        defenses=list(config.defenses),
        has_compare=(store.run_dir / "compare.html").exists(),
    )


def _defense_actions(results) -> str:
    """Total defense actions and their split by condition, e.g. "12 (inject 7, poison 5)"."""
    by_condition: dict[str, int] = {}
    for r in results:
        if r.defense_actions:
            name = r.spec.condition.value
            by_condition[name] = by_condition.get(name, 0) + r.defense_actions
    if not by_condition:
        return "0"
    split = ", ".join(f"{name} {n}" for name, n in sorted(by_condition.items()))
    return f"{sum(by_condition.values())} ({split})"


def write_report(run_dir: Path) -> Path:
    """Write runs/<run_id>/report.html and return its path."""
    path = Path(run_dir) / "report.html"
    path.write_text(build_page(Path(run_dir)), encoding="utf-8")
    return path
