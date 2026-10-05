"""compare.html: the paired comparison of two runs as one self-contained page (SPEC 11).

What: `write_compare(comparison, out_path)` renders a Comparison from analysis/compare.py.
Why: the defense experiment's headline (attack success with and without defenses, McNemar,
the baseline utility cost) should be readable offline and shareable, like report.html.
How: `pruefstand compare RUN_A RUN_B` calls it and writes RUN_B/compare.html. The page uses
the same inline styles as the run report (templates/_style.html.j2), no external assets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pruefstand.analysis.compare import EXACT_BELOW, Comparison, _pct

TEMPLATES = Path(__file__).parent / "templates"


def build_compare_page(comparison: Comparison) -> str:
    """The HTML text of the comparison page."""
    rows = []
    for r in comparison.rows:
        # Pre-formatted cells keep the template free of number formatting.
        rows.append(
            {
                "model": r.model.split("/")[-1],
                "condition": r.condition,
                "variant": r.variant,
                "outcome": r.outcome,
                "n_pairs": r.n_pairs,
                "rate_a": _pct(r.rate_a),
                "rate_b": _pct(r.rate_b),
                "change": _pct(r.change),
                "only_a": r.test.only_a,
                "only_b": r.test.only_b,
                "odds_ratio": f"{r.test.odds_ratio:.2f}",
                "p_value": f"{r.test.p_value:.4f}" + ("" if r.test.exact else "*"),
            }
        )
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return env.get_template("compare.html.j2").render(
        c=comparison,
        rows=rows,
        exact_below=EXACT_BELOW,
        written=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )


def write_compare(comparison: Comparison, out_path: Path) -> Path:
    """Write compare.html and return its path."""
    out_path.write_text(build_compare_page(comparison), encoding="utf-8")
    return out_path
