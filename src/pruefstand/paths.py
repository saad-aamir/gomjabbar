"""Well-known locations inside the repository.

What: the repo root, the vendored MCPMark copy and the cache folder, as Path constants.
Why: several modules need them, and computing them in one place avoids subtle differences.
How: everything is derived from this file's location (src/pruefstand/paths.py).
"""

from pathlib import Path

# src/pruefstand/paths.py -> repo root is three levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]
# The vendored, never-edited MCPMark copy.
MCPMARK_ROOT = REPO_ROOT / "vendor" / "mcpmark"
# Downloaded third-party data (gitignored) and committed caches like paraphrases.
CACHE_ROOT = REPO_ROOT / "cache"
