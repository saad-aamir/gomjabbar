"""Filesystem sandbox: a private copy of a task's initial state for one episode.

What: downloads MCPMark's initial-state zip once per category into cache/mcpmark_states/,
then for each episode copies it to /tmp/gomjabbar/<episode_id>/workspace/ (plus vault/ in
vault conditions) and gives the server command with the right allowed roots.
Why: every episode must start from exactly the state MCPMark creates (SPEC 5.1), and must
never see another episode's changes.
How: mirrors MCPMark's filesystem_state_manager.py: download with curl, `unzip -o` (which
keeps file timestamps that some verifiers check), delete __MACOSX/, then `shutil.copytree`
(which also keeps timestamps). See docs/notes/mcpmark-interface.md.
"""

from __future__ import annotations

import fcntl
import shutil
import subprocess
from pathlib import Path

from gomjabbar.paths import CACHE_ROOT
from gomjabbar.sandbox.canary import plant_filesystem
from gomjabbar.tasks.mcpmark import FILESYSTEM_SERVER_COMMAND

# Where MCPMark serves the zips, one per category.
STATE_URL = "https://storage.mcpmark.ai/filesystem/{category}.zip"
# Where the unpacked categories are cached (gitignored: third-party data).
STATE_CACHE = CACHE_ROOT / "mcpmark_states"
# Parent folder of all episode sandboxes.
SANDBOX_ROOT = Path("/tmp/gomjabbar")


def ensure_category(category: str, cache: Path = STATE_CACHE) -> Path:
    """Return the unpacked initial state of a category, downloading it the first time."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / category
    # A lock file so two concurrent episodes never download the same zip at once.
    with open(cache / f".{category}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_dir():
            return target
        zip_path = cache / f"{category}.zip"
        if not zip_path.exists():
            # curl -f fails on HTTP errors instead of saving an error page as the zip.
            partial = zip_path.with_suffix(".zip.part")
            subprocess.run(
                ["curl", "-fsSL", "-o", str(partial), STATE_URL.format(category=category)],
                check=True,
                timeout=600,
            )
            partial.rename(zip_path)
        # The zip contains the <category>/ folder itself, so unzip into the cache folder.
        subprocess.run(["unzip", "-o", "-q", str(zip_path), "-d", str(cache)], check=True)
        # macOS metadata folder, removed by MCPMark too.
        shutil.rmtree(cache / "__MACOSX", ignore_errors=True)
        if not target.is_dir():
            raise RuntimeError(f"{zip_path} did not contain a {category}/ folder")
        return target


class FilesystemSandbox:
    """The folders of one filesystem episode."""

    def __init__(self, episode_id: str, category: str, root: Path = SANDBOX_ROOT):
        self.category = category
        self.base = root / episode_id
        self.workspace = self.base / "workspace"  # what the task and verify.py see
        self.vault = self.base / "vault"  # only created in vault conditions
        self.has_vault = False

    def create(self, canary: str | None = None) -> None:
        """Copy the initial state into workspace/; plant the vault if a canary is given."""
        source = ensure_category(self.category)
        # Start clean, in case a crashed earlier attempt of this episode left files behind.
        if self.base.exists():
            shutil.rmtree(self.base)
        self.base.mkdir(parents=True)
        # copytree copies with copy2, which keeps modification times, like MCPMark.
        shutil.copytree(source, self.workspace)
        if canary is not None:
            plant_filesystem(self.vault, canary)
            self.has_vault = True

    def server_command(self) -> list[str]:
        """The MCPMark server command with workspace/ (and vault/ if planted) as roots."""
        roots = [str(self.workspace)]
        if self.has_vault:
            roots.append(str(self.vault))
        return [*FILESYSTEM_SERVER_COMMAND, *roots]

    def destroy(self) -> None:
        """Delete the episode's folders."""
        shutil.rmtree(self.base, ignore_errors=True)
