"""Safely borrow the payloads/holdout/ folder for one attack round.

What: a context manager that empties payloads/holdout/, drops in one attacker-written
payload, and on exit restores the folder to exactly what it was before.
Why: Hunter-seeker reuses the holdout run path (it already works end to end), but that
folder holds the M4 held-out evidence. This guarantees those files are never lost, even
if a round crashes.
How: move the existing contents to a temp dir on enter, move them back on exit.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from gomjabbar.payloads import PAYLOAD_ROOT

HOLDOUT_DIR = PAYLOAD_ROOT / "holdout"


@contextmanager
def borrow_holdout(payload_filename: str, payload_text: str):
    """Empty the holdout folder, write one payload, yield, then restore the original files.

    payload_filename: the .yaml file name to create (e.g. "attack.yaml").
    payload_text: the full YAML contents of the attacker's payload.
    """
    # 1. Snapshot: move every current file out to a temp dir we control.
    stash = Path(tempfile.mkdtemp(prefix="holdout-stash-"))
    originals = list(HOLDOUT_DIR.iterdir())
    for item in originals:
        shutil.move(str(item), str(stash / item.name))
    try:
        # 2. Drop in exactly the attacker's one payload for this round.
        (HOLDOUT_DIR / payload_filename).write_text(payload_text, encoding="utf-8")
        yield HOLDOUT_DIR
    finally:
        # 3. Restore: wipe whatever we put in, move the originals back. Always runs,
        #    even if the round raised, so the M4 files can never be left missing.
        for item in list(HOLDOUT_DIR.iterdir()):
            item.unlink()
        for item in list(stash.iterdir()):
            shutil.move(str(item), str(HOLDOUT_DIR / item.name))
        shutil.rmtree(stash, ignore_errors=True)
