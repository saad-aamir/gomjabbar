"""Hand-drawn SVG dot-and-interval charts for the HTML report (no external assets).

What: `dot_interval_chart(rows, series)` draws one row per category (a condition or a fault
profile) with one dot per model at its mean and a line for its 95% interval, on a shared
0 to 100% axis.
Why: SPEC 12 asks for a self-contained page that opens offline, so the charts are plain SVG
written as text. Dots with intervals show both the value and its uncertainty, which is the
point of the report.
How: colors are CSS variables (--series-1, --series-2, ...) defined in the template for
light and dark mode, so the chart follows the page theme. Each mark carries an SVG <title>,
which browsers show as a tooltip on hover.
"""

from __future__ import annotations

from html import escape

from pruefstand.analysis.stats import Estimate

# Layout in SVG units; the SVG scales to the page width.
WIDTH = 640
LABEL_W = 130  # left column for row labels
RIGHT_PAD = 24
ROW_H = 34
TOP = 28  # room for the axis labels
DOT_R = 5


def _x(value: float) -> float:
    """Map a fraction 0..1 to an x coordinate inside the plot area."""
    return LABEL_W + value * (WIDTH - LABEL_W - RIGHT_PAD)


def dot_interval_chart(
    categories: list[str], series: list[tuple[str, dict[str, Estimate]]], title: str
) -> str:
    """An SVG chart. `series` is [(model, {category: estimate})], in legend order."""
    height = TOP + ROW_H * len(categories) + 12
    parts = [
        f'<svg class="chart" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
    ]
    # Recessive grid and axis labels at 0, 25, 50, 75 and 100%.
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        x = _x(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{TOP - 6}" x2="{x:.1f}" y2="{height - 8}" class="grid"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{TOP - 12}" class="axis" text-anchor="middle">'
            f"{int(tick * 100)}%</text>"
        )
    # Dots of different models in one row are offset vertically so they never overlap.
    offsets = [(i - (len(series) - 1) / 2) * 10 for i in range(len(series))]
    for row, category in enumerate(categories):
        y_mid = TOP + ROW_H * row + ROW_H / 2
        parts.append(
            f'<text x="{LABEL_W - 10}" y="{y_mid + 4:.1f}" class="label" text-anchor="end">'
            f"{escape(category)}</text>"
        )
        for index, (model, points) in enumerate(series):
            estimate = points.get(category)
            if estimate is None or estimate.n_tasks == 0:
                continue
            y = y_mid + offsets[index]
            tip = (
                f"{model} | {category}: {100 * estimate.value:.1f}% "
                f"[{100 * estimate.low:.1f}, {100 * estimate.high:.1f}], "
                f"{estimate.n_tasks} tasks"
            )
            color = f"var(--series-{index + 1})"
            parts.append(f"<g><title>{escape(tip)}</title>")
            # Interval line, then the dot with a surface-colored ring.
            parts.append(
                f'<line x1="{_x(estimate.low):.1f}" y1="{y:.1f}" x2="{_x(estimate.high):.1f}" '
                f'y2="{y:.1f}" stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
            )
            parts.append(
                f'<circle cx="{_x(estimate.value):.1f}" cy="{y:.1f}" r="{DOT_R}" '
                f'fill="{color}" class="dot"/>'
            )
            # A wider transparent hit target so the tooltip is easy to reach.
            parts.append(
                f'<rect x="{_x(estimate.low) - 6:.1f}" y="{y - 8:.1f}" '
                f'width="{_x(estimate.high) - _x(estimate.low) + 12:.1f}" height="16" '
                f'fill="transparent"/>'
            )
            parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
