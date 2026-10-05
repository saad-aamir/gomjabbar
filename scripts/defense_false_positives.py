"""Measure the defenses' false positives on clean text, for any run folders.

What: runs pinning, the sanitizer and the description scan over the real servers' clean tool
descriptions and over every clean tool result in the given run folders, and writes a Markdown
report with the counts and every removal.
Why: the M4 goal is zero removals on clean text. Saad's Mac keeps the full `traces/` folders
of the runs (the repo only commits notable traces), so the same script must work on both.
How:
    uv run python scripts/defense_false_positives.py runs/dev-20261003-205231 \
        --out docs/notes/defense-false-positives-m3-full.md
With no run folder it reads every folder under runs/. With no --out it prints the report.
Full traces in `traces/` are used where they exist, otherwise the committed `notable/` and
`grader_errors/` traces. See src/pruefstand/defenses/false_positives.py for the rules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pruefstand.defenses.false_positives import measure, render_markdown
from pruefstand.paths import REPO_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("runs", nargs="*", type=Path, help="run folders (default: all in runs/)")
    parser.add_argument("--out", type=Path, help="write the report here instead of printing it")
    args = parser.parse_args(argv)
    run_dirs = args.runs or sorted(p for p in (REPO_ROOT / "runs").iterdir() if p.is_dir())
    # Refuse a path that is not a run folder rather than silently measuring nothing.
    for run_dir in run_dirs:
        if not (run_dir / "results.jsonl").exists():
            print(f"not a run folder (no results.jsonl): {run_dir}", file=sys.stderr)
            return 2
    report = render_markdown(measure(run_dirs))
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
