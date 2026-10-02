"""A tiny MCP server for tests: notes stored in a JSON file.

What: a FastMCP server with four tools, read_note, write_note, list_notes and delete_note
(SPEC 13). The notes live in a JSON file whose path comes from the env var FAKE_NOTES_PATH.
Why: proxy and agent loop tests need a real MCP server that starts fast, needs no network
and whose final state is easy to check.
How: tests start it with `python tests/fixtures/fake_server.py`, usually behind the proxy,
and read the JSON file afterwards to grade the episode. Two optional test hooks:
FAKE_ENV_DUMP (write the server's environment variable names to that file at start) and
FAKE_CRASH_ON_DELETE (exit the process when delete_note is called, to kill the transport).
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# The server name the client sees during initialize.
mcp = FastMCP("fake-notes")


def _path() -> Path:
    """Where the notes file lives; set by the test through the environment."""
    return Path(os.environ["FAKE_NOTES_PATH"])


def _load() -> dict[str, str]:
    """Read all notes, or an empty dict if the file does not exist yet."""
    path = _path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(notes: dict[str, str]) -> None:
    """Write all notes back to the file."""
    _path().write_text(json.dumps(notes, sort_keys=True), encoding="utf-8")


@mcp.tool()
def list_notes() -> str:
    """List the names of all notes, one per line."""
    return "\n".join(sorted(_load())) or "(no notes)"


@mcp.tool()
def read_note(name: str) -> str:
    """Return the text of the note with this name."""
    notes = _load()
    if name not in notes:
        # Raising makes FastMCP answer with isError: true, like a real tool failure.
        raise ValueError(f"no note named {name}")
    return notes[name]


@mcp.tool()
def write_note(name: str, text: str) -> str:
    """Create or overwrite the note with this name."""
    notes = _load()
    notes[name] = text
    _save(notes)
    return f"wrote {name}"


@mcp.tool()
def delete_note(name: str) -> str:
    """Delete the note with this name."""
    if os.environ.get("FAKE_CRASH_ON_DELETE"):
        # Simulate a server crash: the process dies without answering.
        os._exit(3)
    notes = _load()
    notes.pop(name, None)
    _save(notes)
    return f"deleted {name}"


if __name__ == "__main__":
    # Test hook: record which environment variables the server can see.
    if os.environ.get("FAKE_ENV_DUMP"):
        Path(os.environ["FAKE_ENV_DUMP"]).write_text("\n".join(sorted(os.environ)))
    # stdio transport: JSON-RPC lines on stdin and stdout.
    mcp.run("stdio")
