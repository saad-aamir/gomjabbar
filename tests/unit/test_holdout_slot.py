"""The holdout-borrow helper must always restore the original folder contents."""

from gomjabbar.redteam.holdout_slot import HOLDOUT_DIR, borrow_holdout


def _snapshot():
    """Map of filename -> contents for everything currently in the holdout folder."""
    return {p.name: p.read_text() for p in HOLDOUT_DIR.iterdir()}


def test_originals_restored_after_normal_use():
    before = _snapshot()
    with borrow_holdout("attack.yaml", "id: test\n") as folder:
        # During the borrow, only our one file is present.
        assert {p.name for p in folder.iterdir()} == {"attack.yaml"}
    assert _snapshot() == before          # folder is back exactly as it was


def test_originals_restored_even_if_the_round_crashes():
    before = _snapshot()
    try:
        with borrow_holdout("attack.yaml", "id: test\n"):
            raise RuntimeError("round blew up")
    except RuntimeError:
        pass
    assert _snapshot() == before          # restored despite the crash
