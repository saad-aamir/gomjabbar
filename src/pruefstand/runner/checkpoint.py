"""Git checkpoints during long runs: commit and push the committed artifacts every N episodes.

What: `git add` the files of a run that belong in git, commit, and push to the current branch.
Why: cloud VMs are reclaimed after inactivity and lose uncommitted files (SPEC 4,
Persistence). A failed push is logged and retried at the next checkpoint, never fatal.
How: the grid runner calls `checkpoint(store, done, total)` every N completed episodes and
once at the end.
"""

from __future__ import annotations

import subprocess

from pruefstand.paths import REPO_ROOT
from pruefstand.runner.store import RunStore

# Files and folders of a run that are committed; traces/ and proxy/ stay local.
COMMITTED = ["config.yaml", "results.jsonl", "run.log", "quota.json", "report.html", "notable"]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def git_commit_id() -> str:
    """The current commit, with "-dirty" if tracked files have uncommitted changes.

    Files under runs/ are run output, not code: a run in progress keeps changing its
    results.jsonl and run.log, which must not mark the code as dirty.
    """
    head = _git("rev-parse", "HEAD").stdout.strip() or "unknown"
    dirty = _git(
        "status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)runs"
    ).stdout.strip()
    return f"{head}-dirty" if dirty else head


def checkpoint(store: RunStore, done: int, total: int) -> bool:
    """Commit and push the run's committed artifacts. Returns True if the push worked."""
    store.trim_log()
    paths = [str(store.run_dir / name) for name in COMMITTED if (store.run_dir / name).exists()]
    _git("add", "-f", *paths)
    # Nothing staged means nothing changed since the last checkpoint.
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return True
    message = f"run({store.run_id}): checkpoint {done}/{total}"
    commit = _git("commit", "-m", message, "--", *paths)
    if commit.returncode != 0:
        store.log(f"checkpoint commit failed: {commit.stderr.strip()[:300]}")
        return False
    push = _git("push", "-u", "origin", "HEAD")
    if push.returncode != 0:
        # Not fatal: the next checkpoint pushes this commit too.
        store.log(f"checkpoint push failed, will retry: {push.stderr.strip()[:300]}")
        return False
    store.log(f"checkpoint pushed: {message}")
    return True
