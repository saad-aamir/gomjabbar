"""Capture the clean tool lists of the real MCP servers, for the defense false-positive check.

What: starts the official filesystem MCP server and postgres-mcp exactly as the sandbox does
(no proxy, no poison, no model), calls `list_tools` once on each, and saves the tools as JSON
in `tests/fixtures/clean_tools/<service>.json`.
Why: the traces do not store tool descriptions, but the false-positive measurement for
description_scan (and the other two defenses) needs every real, clean description. Saving
them as fixtures also lets a unit test assert zero removals without starting any server.
How: `uv run python scripts/capture_clean_tools.py`. The filesystem server gets an empty
temporary directory as its root; postgres-mcp gets the `postgres` maintenance database of the
local `pruefstand-pg` container (started on demand). No model is called.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gomjabbar.agent.loop import server_environment
from gomjabbar.paths import REPO_ROOT
from gomjabbar.sandbox.postgres import POSTGRES_SERVER_COMMAND, database_uri, ensure_container
from gomjabbar.tasks.mcpmark import FILESYSTEM_SERVER_COMMAND

# Where the fixtures go; tests/unit/test_defenses.py reads them.
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "clean_tools"


async def list_tools(command: list[str], env_extra: dict[str, str]) -> list[dict]:
    """Start one server, list its tools, and return them as plain dicts."""
    params = StdioServerParameters(
        command=command[0], args=command[1:], env=server_environment(env_extra)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    # Only the fields the model would see: name, description and input schema.
    return [
        {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
        for t in listed.tools
    ]


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Filesystem: an empty temporary root is enough, the tool list does not depend on it.
    with tempfile.TemporaryDirectory() as root:
        tools = await list_tools([*FILESYSTEM_SERVER_COMMAND, root], {})
    (OUT_DIR / "filesystem.json").write_text(json.dumps(tools, indent=2) + "\n", "utf-8")
    print(f"filesystem: {len(tools)} tools")
    # Postgres: the maintenance database of the local container.
    ensure_container()
    tools = await list_tools(POSTGRES_SERVER_COMMAND, {"DATABASE_URI": database_uri("postgres")})
    (OUT_DIR / "postgres.json").write_text(json.dumps(tools, indent=2) + "\n", "utf-8")
    print(f"postgres: {len(tools)} tools")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
