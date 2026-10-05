"""False-positive measurement for the three defenses: how often they touch clean text.

What: collects clean text (the real servers' tool descriptions, and every tool result in the
traces of one or more run folders that carries no attack payload), runs every defense over
it, and counts what each defense would have removed. `render_markdown` turns the counts and
every removal into a report.
Why: a defense that strips ordinary file contents or tool documentation breaks the agent's
work. The goal is zero removals on clean text (M4, Saad). Measuring it on real outputs from
the M1 to M3 runs shows whether the patterns are too broad before a paid run uses them.
How: scripts/defense_false_positives.py calls `collect_tool_results` for each run folder it
is given and `measure`. It reads full traces from `traces/` when they exist (Saad's Mac keeps
them) and otherwise the committed `notable/` and `grader_errors/` traces, one per episode.
Tool results that contain a sentence of any payload file are attack text, not clean text,
and are counted separately instead of measured.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pruefstand.defenses.description_scan import clean_text, scan_tools
from pruefstand.defenses.pinning import ToolPin
from pruefstand.defenses.sanitizer import sanitize
from pruefstand.paths import REPO_ROOT

PAYLOAD_ROOT = REPO_ROOT / "payloads"
CLEAN_TOOLS = REPO_ROOT / "tests" / "fixtures" / "clean_tools"
# Payload sentences shorter than this are too generic to mark a result as attack text.
MIN_MARKER_CHARS = 20


@dataclass
class Removal:
    """One thing a defense would have removed from clean text."""

    defense: str
    source: str  # "tool description" or "tool result"
    where: str  # tool name, or run/episode/tool for a result
    text: str
    pattern: str


@dataclass
class Measurement:
    """Counts of clean texts checked and removals per defense."""

    descriptions: int = 0  # tool descriptions and parameter descriptions checked
    results: int = 0  # clean tool results checked
    unique_results: int = 0  # distinct clean tool-result texts
    attack_results: int = 0  # results skipped because they carry payload text
    episodes: int = 0  # episodes whose traces were read
    runs: list[str] = field(default_factory=list)
    trace_sources: Counter = field(default_factory=Counter)  # "traces", "notable", ...
    removals: list[Removal] = field(default_factory=list)

    def count(self, defense: str, source: str) -> int:
        return sum(1 for r in self.removals if r.defense == defense and r.source == source)


# ---- inputs -----------------------------------------------------------------------------


def payload_markers(root: Path = PAYLOAD_ROOT) -> list[str]:
    """Sentences of every payload file (all folders), used to recognise attack text."""
    markers = []
    for path in sorted(root.rglob("*.yaml")):
        if path.name == "pushback.yaml":
            continue
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        text = str(data.get("text") or "")
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            sentence = sentence.strip()
            if len(sentence) >= MIN_MARKER_CHARS:
                markers.append(sentence)
    return markers


def clean_tool_lists(root: Path = CLEAN_TOOLS) -> dict[str, list[dict]]:
    """The real servers' tool lists (OpenAI format), by service."""
    lists = {}
    for path in sorted(root.glob("*.json")):
        tools = json.loads(path.read_text("utf-8"))
        lists[path.stem] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["inputSchema"],
                },
            }
            for t in tools
        ]
    return lists


def _read_events(path: Path) -> list[dict]:
    """Trace events from a .jsonl or .jsonl.gz file."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def trace_files(run_dir: Path) -> dict[str, tuple[str, Path]]:
    """One trace file per episode id: full traces/ first, then notable/, then grader_errors/."""
    found: dict[str, tuple[str, Path]] = {}
    for folder in ("traces", "notable", "grader_errors"):
        for path in sorted((run_dir / folder).glob("*.jsonl*")):
            episode_id = path.name.split(".")[0]
            # The first folder that has an episode wins, so a full trace beats a notable copy.
            found.setdefault(episode_id, (folder, path))
    return found


def collect_tool_results(run_dir: Path, measurement: Measurement) -> list[tuple[str, str]]:
    """(where, text) for every tool result in one run's traces, recording sources."""
    results = []
    measurement.runs.append(run_dir.name)
    for episode_id, (folder, path) in sorted(trace_files(run_dir).items()):
        measurement.episodes += 1
        measurement.trace_sources[folder] += 1
        for event in _read_events(path):
            if event.get("kind") != "tool_result":
                continue
            payload = event.get("payload") or {}
            text = str(payload.get("text") or "")
            where = f"{run_dir.name}/{episode_id}/{payload.get('name', '?')}"
            results.append((where, text))
    return results


# ---- measurement ------------------------------------------------------------------------


def measure_descriptions(tool_lists: dict[str, list[dict]], m: Measurement) -> None:
    """Run all three defenses over the clean tool lists."""
    for service, tools in tool_lists.items():
        # description_scan: the defense as the loop runs it.
        _, actions = scan_tools(tools)
        for action in actions:
            if action.action == "removed_sentence":
                m.removals.append(
                    Removal(
                        "description_scan",
                        "tool description",
                        f"{service}/{action.tool} {action.where}",
                        action.text,
                        action.pattern,
                    )
                )
        # pinning: pin the list, then present the same list again. Any action is a false alarm.
        _, changes = ToolPin(tools).check(tools)
        for change in changes:
            m.removals.append(
                Removal(
                    "pinning", "tool description", f"{service}/{change.tool}", change.change, ""
                )
            )
        # sanitizer: as if the description text had arrived as a tool result.
        for tool in tools:
            function = tool["function"]
            texts = [("description", function.get("description") or "")]
            for name, prop in ((function.get("parameters") or {}).get("properties") or {}).items():
                if isinstance(prop, dict) and isinstance(prop.get("description"), str):
                    texts.append((f"param:{name}", prop["description"]))
            for where, text in texts:
                m.descriptions += 1
                for removal in sanitize(text)[1]:
                    m.removals.append(
                        Removal(
                            "sanitizer",
                            "tool description",
                            f"{service}/{function['name']} {where}",
                            removal.text,
                            removal.pattern,
                        )
                    )


def measure_results(results: list[tuple[str, str]], markers: list[str], m: Measurement) -> None:
    """Run the sanitizer and the description scan's text cleaner over clean tool results."""
    seen: set[str] = set()
    for where, text in results:
        # Attack text is not clean text: skip results that carry a payload sentence.
        if any(marker in text for marker in markers):
            m.attack_results += 1
            continue
        m.results += 1
        if text in seen:
            continue  # identical texts (same file read twice) are checked once
        seen.add(text)
        for removal in sanitize(text)[1]:
            m.removals.append(
                Removal("sanitizer", "tool result", where, removal.text, removal.pattern)
            )
        for removed, pattern in clean_text(text)[1]:
            m.removals.append(Removal("description_scan", "tool result", where, removed, pattern))
    m.unique_results = len(seen)


def measure(run_dirs: list[Path]) -> Measurement:
    """The whole measurement over the clean tool lists and the given run folders."""
    m = Measurement()
    measure_descriptions(clean_tool_lists(), m)
    markers = payload_markers()
    results = []
    for run_dir in run_dirs:
        results.extend(collect_tool_results(run_dir, m))
    measure_results(results, markers, m)
    return m


def render_markdown(m: Measurement) -> str:
    """The report: counts per defense and source, then every removal."""
    lines = [
        "# Defense false positives on clean text",
        "",
        "Written by `scripts/defense_false_positives.py`. Each defense is run over clean text:",
        "the real servers' tool descriptions (`tests/fixtures/clean_tools/`) and every tool",
        "result in the run folders below that carries no payload sentence. A removal on clean",
        "text is a false positive. The goal is zero.",
        "",
        f"- Runs: {', '.join(m.runs) or 'none'}",
        f"- Episodes read: {m.episodes} (trace source: "
        + ", ".join(f"{k} {v}" for k, v in sorted(m.trace_sources.items()))
        + ")",
        f"- Tool and parameter descriptions: {m.descriptions}",
        f"- Clean tool results: {m.results} ({m.unique_results} distinct texts checked)",
        f"- Results skipped as attack text (contain a payload sentence): {m.attack_results}",
        "",
        "| Defense | Removals on tool descriptions | Removals on tool results |",
        "| --- | --- | --- |",
    ]
    for defense in ("pinning", "sanitizer", "description_scan"):
        results = "n/a" if defense == "pinning" else m.count(defense, "tool result")
        lines.append(f"| {defense} | {m.count(defense, 'tool description')} | {results} |")
    lines += ["", "## Every removal", ""]
    if not m.removals:
        lines.append("None.")
    for r in m.removals:
        snippet = r.text.replace("\n", " ")[:200]
        lines.append(
            f'- **{r.defense}**, {r.source}, `{r.where}`: "{snippet}" (pattern `{r.pattern}`)'
        )
    return "\n".join(lines) + "\n"
