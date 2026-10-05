"""The defense patterns are frozen at the git tag defense-patterns-v1 (DEVIATIONS.md, 2026-10-04).

The holdout experiment is only fair if the patterns stay exactly as they were before the
holdout payloads were written. This test fails if patterns.py changes by a single byte.
Changing it on purpose means a new tag, a new DEVIATIONS.md entry and a new hash here.
"""

import hashlib

from gomjabbar.paths import REPO_ROOT

# sha256 of src/pruefstand/defenses/patterns.py at commit 91a7f673 (tag defense-patterns-v1).
# The rename to Gom Jabbar (DEVIATIONS.md, 2026-10-05) moved the file byte-for-byte to
# src/gomjabbar/defenses/patterns.py, so the hash is unchanged.
FROZEN_SHA256 = "2c623a34a07214fd8622bdfad10d1874f754d0cee40839759e796888881ad79e"


def test_defense_patterns_are_unchanged_since_the_freeze():
    data = (REPO_ROOT / "src" / "gomjabbar" / "defenses" / "patterns.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == FROZEN_SHA256
