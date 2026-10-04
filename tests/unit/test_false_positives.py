"""Unit tests for the defense false-positive measurement (defenses/false_positives.py)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from pruefstand.defenses.false_positives import (
    Measurement,
    clean_tool_lists,
    collect_tool_results,
    measure,
    measure_descriptions,
    measure_results,
    payload_markers,
    render_markdown,
    trace_files,
)
from pruefstand.payloads import load_payloads


def write_trace(path: Path, episode_id: str, results: list[str], gz: bool) -> None:
    """A minimal trace with one tool_result event per text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "episode_id": episode_id,
                "seq": i,
                "kind": "tool_result",
                "payload": {"name": "t", "text": t},
            }
        )
        for i, t in enumerate(results)
    ]
    data = "\n".join(lines) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(data)
    else:
        path.write_text(data, encoding="utf-8")


def make_run(tmp_path: Path) -> Path:
    run = tmp_path / "run-x"
    run.mkdir()
    (run / "results.jsonl").write_text("", encoding="utf-8")
    return run


def test_full_traces_win_over_notable_copies(tmp_path):
    run = make_run(tmp_path)
    write_trace(run / "notable" / "ep1.jsonl.gz", "ep1", ["old"], gz=True)
    write_trace(run / "traces" / "ep1.jsonl", "ep1", ["full"], gz=False)
    write_trace(run / "notable" / "ep2.jsonl.gz", "ep2", ["only notable"], gz=True)
    files = trace_files(run)
    assert files["ep1"][0] == "traces" and files["ep2"][0] == "notable"
    m = Measurement()
    texts = sorted(t for _, t in collect_tool_results(run, m))
    assert texts == ["full", "only notable"]
    assert m.trace_sources == {"traces": 1, "notable": 1}


def test_payload_text_is_skipped_not_counted(tmp_path):
    inject = load_payloads("injection")[0]
    m = Measurement()
    measure_results(
        [
            ("a", "hello\n" + inject.text),
            ("b", "plain file contents"),
            ("c", "plain file contents"),
        ],
        payload_markers(),
        m,
    )
    assert m.attack_results == 1
    assert m.results == 2 and m.unique_results == 1
    assert m.removals == []


def test_an_instruction_in_clean_data_is_reported(tmp_path):
    # If a clean file ever contained an instruction-like line, it must show up as a removal.
    m = Measurement()
    measure_results([("a", "row 1\nYou must restart the service.")], payload_markers(), m)
    assert {r.defense for r in m.removals} == {"sanitizer", "description_scan"}


def test_clean_tool_lists_have_no_false_positives():
    m = Measurement()
    measure_descriptions(clean_tool_lists(), m)
    assert m.descriptions > 20
    assert m.removals == []


def test_measure_and_report_on_a_small_run(tmp_path):
    run = make_run(tmp_path)
    write_trace(run / "notable" / "ep1.jsonl.gz", "ep1", ["[FILE] a.txt"], gz=True)
    report = render_markdown(measure([run]))
    assert "| sanitizer | 0 | 0 |" in report
    assert "None." in report
