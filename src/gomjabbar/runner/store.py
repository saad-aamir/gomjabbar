"""On-disk storage for one run: config, results, traces, proxy logs and the run log.

What: a small class that owns the folder runs/<run_id>/ and every file inside it (SPEC 4).
Why: resume depends on results.jsonl being append-only and written only after grading
succeeds, so all writes go through one place that enforces this.
How: the runner creates a RunStore, asks it which episodes are already done, and after each
graded episode calls `write_trace` and then `append_result`. Analysis reads `read_results`.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from gomjabbar.config import RunConfig, dump_config
from gomjabbar.models import EpisodeResult, TraceEvent

# How many lines of run.log are kept when it is committed (SPEC 4, Persistence).
RUN_LOG_KEEP_LINES = 5000


class RunStore:
    """Files of one run under runs/<run_id>/."""

    def __init__(self, run_dir: Path | str):
        # Root folder of the run, created if missing.
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # The run id is the folder name.
        self.run_id = self.run_dir.name

    # ---- paths -------------------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self.run_dir / "config.yaml"

    @property
    def results_path(self) -> Path:
        return self.run_dir / "results.jsonl"

    @property
    def log_path(self) -> Path:
        return self.run_dir / "run.log"

    @property
    def quota_path(self) -> Path:
        return self.run_dir / "quota.json"

    def trace_path(self, episode_id: str) -> Path:
        return self.run_dir / "traces" / f"{episode_id}.jsonl"

    def proxy_log_path(self, episode_id: str) -> Path:
        return self.run_dir / "proxy" / f"{episode_id}.jsonl"

    def notable_path(self, episode_id: str) -> Path:
        return self.run_dir / "notable" / f"{episode_id}.jsonl.gz"

    def grader_error_path(self, episode_id: str) -> Path:
        return self.run_dir / "grader_errors" / f"{episode_id}.jsonl.gz"

    # ---- config ------------------------------------------------------------------------

    def write_config(self, config: RunConfig) -> None:
        """Write the resolved config. Refuse if a different config is already stored."""
        text = dump_config(config)
        if self.config_path.exists() and self.config_path.read_text(encoding="utf-8") != text:
            # Resuming with changed settings would mix two experiments in one results file.
            raise ValueError(f"{self.config_path} exists with a different config; refusing")
        self.config_path.write_text(text, encoding="utf-8")

    # ---- results -----------------------------------------------------------------------

    def completed_ids(self) -> set[str]:
        """Episode ids that already have a result. Used by resume to skip them."""
        return {result.episode_id for result in self.read_results()}

    def read_results(self) -> list[EpisodeResult]:
        """Every result row, in file order."""
        if not self.results_path.exists():
            return []
        results = []
        with open(self.results_path, encoding="utf-8") as handle:
            for line in handle:
                # A partly written last line (crash mid-write) is skipped; its episode reruns.
                if not line.endswith("\n"):
                    continue
                results.append(EpisodeResult.model_validate_json(line))
        return results

    def append_result(self, result: EpisodeResult) -> None:
        """Append one graded result as one line, flushed to disk before returning.

        This is the only way results reach the file, and the runner calls it only after every
        grader succeeded, so a grader error can never leave a partial or default row behind.
        """
        line = result.model_dump_json() + "\n"
        self._repair_partial_last_line()
        with open(self.results_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            # fsync so a killed process cannot lose a row it already reported as written.
            os.fsync(handle.fileno())

    def _repair_partial_last_line(self) -> None:
        """Cut off a half-written last line left by a crash, so the next row starts cleanly."""
        if not self.results_path.exists():
            return
        data = self.results_path.read_bytes()
        if data and not data.endswith(b"\n"):
            # Keep everything up to and including the last complete line.
            cut = data.rfind(b"\n") + 1
            self.results_path.write_bytes(data[:cut])

    # ---- traces ------------------------------------------------------------------------

    def write_trace(self, episode_id: str, events: list[TraceEvent]) -> Path:
        """Write an episode's full trace (gitignored)."""
        path = self.trace_path(episode_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(event.model_dump_json() + "\n")
        return path

    def save_notable(self, episode_id: str) -> Path:
        """Copy a trace, gzipped, to notable/ so it is committed (SPEC 4, Persistence)."""
        source = self.trace_path(episode_id)
        target = self.notable_path(episode_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0 keeps the gzip bytes identical for identical traces.
        with open(source, "rb") as src, open(target, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as dst:
                dst.write(src.read())
        return target

    def save_grader_error_trace(self, episode_id: str, events: list[TraceEvent]) -> Path:
        """Save the trace of an episode whose grading failed, gzipped, for debugging.

        It goes to grader_errors/, not traces/ or notable/: no result row exists for the
        episode (a grader error must never produce one), and a later rerun's own trace must
        not be mistaken for this one. The folder is committed by checkpoints so the evidence
        survives a reclaimed cloud VM.
        """
        target = self.grader_error_path(episode_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = "".join(event.model_dump_json() + "\n" for event in events).encode("utf-8")
        with open(target, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as dst:
                dst.write(data)
        return target

    # ---- run log -----------------------------------------------------------------------

    def log(self, message: str, episode_id: str | None = None) -> None:
        """Append one line to run.log, tagged with the episode id when there is one."""
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        tag = f"[{episode_id}] " if episode_id else ""
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {tag}{message}\n")

    def trim_log(self) -> None:
        """Keep only the last RUN_LOG_KEEP_LINES lines of run.log before a commit."""
        if not self.log_path.exists():
            return
        lines = self.log_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > RUN_LOG_KEEP_LINES:
            self.log_path.write_text("".join(lines[-RUN_LOG_KEEP_LINES:]), encoding="utf-8")

    # ---- small JSON state files ----------------------------------------------------------

    def read_json(self, path: Path, default: dict) -> dict:
        """Read a small JSON state file, or return the default if it does not exist."""
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, data: dict) -> None:
        """Write a small JSON state file atomically (write to a temp file, then rename)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
