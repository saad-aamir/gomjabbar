"""State grader: runs MCPMark's verify.py on the final state.

What: runs a task's verify.py in a subprocess, exactly as MCPMark does, and returns pass or
fail with the tail of its output.
Why: correctness is judged by the final state, never by the agent's words (SPEC 1). Because
MCPMark verifiers exit 1 both for a failed task and for a broken setup, this grader checks
its own preconditions first and raises GraderError for anything that looks like a harness
problem. A GraderError aborts the write of the result and stops the run (CLAUDE.md).
How: the episode runner calls `grade_filesystem(task, workspace)` or
`grade_postgres(task, database)` after the agent finishes.
Rules are in docs/notes/mcpmark-interface.md ("Exit codes").
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pruefstand.models import Service, Task

# verify.py timeout (SPEC 5.4; MCPMark itself uses 300 s).
VERIFY_TIMEOUT_S = 600
# How much of the verifier's output is kept in the result row.
STDOUT_TAIL_CHARS = 2000
# Signs in stderr that the verifier could not run at all (a broken environment), as opposed
# to a verifier that raised while inspecting a wrong state. Some verifiers do not catch their
# own exceptions: file_splitting raises FileNotFoundError when the agent never created split/,
# exit 1 with a traceback, which MCPMark (and we) count as a task failure.
CRASH_MARKERS = ("ModuleNotFoundError", "ImportError", "SyntaxError")


class GraderError(Exception):
    """A harness problem while grading. The result must not be written."""


@dataclass
class StateVerdict:
    passed: bool
    stdout_tail: str


def _verify_environment(extra: dict[str, str]) -> dict[str, str]:
    """os.environ plus the task variables, like MCPMark, minus our PFS_ API keys."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PFS_")}
    env.update(extra)
    return env


def run_verify(task: Task, extra_env: dict[str, str]) -> StateVerdict:
    """Run verify.py with the given task variables and interpret the exit code."""
    verify = task.source_dir / "verify.py"
    if not verify.is_file():
        raise GraderError(f"verify.py missing for {task.id}")
    try:
        proc = subprocess.run(
            [sys.executable, str(verify)],
            env=_verify_environment(extra_env),
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraderError(f"verify.py timed out after {VERIFY_TIMEOUT_S}s for {task.id}") from exc

    output = (proc.stdout or "") + (proc.stderr or "")
    tail = output[-STDOUT_TAIL_CHARS:]
    # Any exit code other than 0 (pass) or 1 (fail) is not a verdict.
    if proc.returncode not in (0, 1):
        raise GraderError(f"verify.py exited {proc.returncode} for {task.id}: {tail[-500:]}")
    # A verifier that cannot even import is a harness problem, not a task failure.
    if any(marker in (proc.stderr or "") for marker in CRASH_MARKERS):
        raise GraderError(f"verify.py crashed for {task.id}: {proc.stderr[-500:]}")
    return StateVerdict(passed=proc.returncode == 0, stdout_tail=tail)


def grade_filesystem(task: Task, workspace: Path) -> StateVerdict:
    """Grade a filesystem task. Checks preconditions first, then runs verify.py."""
    if task.service != Service.FILESYSTEM:
        raise GraderError(f"{task.id} is not a filesystem task")
    if not workspace.is_dir():
        raise GraderError(f"workspace {workspace} does not exist")
    # FILESYSTEM_TEST_DIR is the only variable filesystem verifiers read.
    return run_verify(task, {"FILESYSTEM_TEST_DIR": str(workspace)})


def grade_postgres(task: Task, database: str) -> StateVerdict:
    """Grade a postgres task against the episode's own database.

    Preconditions first (a missing database is a harness problem, not a task failure),
    then verify.py with the POSTGRES_* variables MCPMark sets.
    """
    # Imported here so filesystem-only runs never need the postgres module.
    from pruefstand.sandbox.postgres import database_exists, verify_environment

    if task.service != Service.POSTGRES:
        raise GraderError(f"{task.id} is not a postgres task")
    try:
        exists = database_exists(database)
    except Exception as exc:  # noqa: BLE001 - the service itself is unreachable
        raise GraderError(f"postgres unreachable while grading {task.id}: {exc}") from exc
    if not exists:
        raise GraderError(f"episode database {database} does not exist")
    return run_verify(task, verify_environment(database))
