"""Integration tests for the postgres sandbox and server, against the local container.

Marked `postgres`: skipped in CI (no Docker there, SPEC 13) and wherever the container cannot
be started (tests/conftest.py). In the cloud session they use the same container and
templates as real runs.
"""

import asyncio

import pytest

from gomjabbar.sandbox import postgres
from gomjabbar.sandbox.postgres import PostgresSandbox

# Needs the container; skipped in CI and without Docker (tests/conftest.py).
pytestmark = pytest.mark.postgres

# A real easy task's category, so the test uses a real MCPMark template.
CATEGORY = "chinook"


def query(database: str, sql: str):
    conn = postgres._connect(database)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall() if cur.description else None
    finally:
        conn.close()


def test_concurrent_episodes_on_the_same_task_are_isolated(tmp_path):
    # Two episodes of the same task, alive at the same time.
    first = PostgresSandbox("isolation_a", CATEGORY, tmp_path)
    second = PostgresSandbox("isolation_b", CATEGORY, tmp_path)
    first.create()
    second.create()
    try:
        before = query(second.database, 'SELECT count(*) FROM "Customer"')[0][0]
        # Episode A writes: a new table and a changed row.
        query(first.database, "CREATE TABLE written_by_a (x int)")
        query(first.database, 'UPDATE "Customer" SET "FirstName" = \'Changed by A\'')
        # Episode B sees none of it.
        tables = query(
            second.database,
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'written_by_a'",
        )
        assert tables[0][0] == 0
        changed = query(
            second.database, 'SELECT count(*) FROM "Customer" WHERE "FirstName" = \'Changed by A\''
        )
        assert changed[0][0] == 0
        assert query(second.database, 'SELECT count(*) FROM "Customer"')[0][0] == before
        # And the template itself is untouched for the next episode.
        third = PostgresSandbox("isolation_c", CATEGORY, tmp_path)
        third.create()
        try:
            changed = query(
                third.database,
                'SELECT count(*) FROM "Customer" WHERE "FirstName" = \'Changed by A\'',
            )
            assert changed[0][0] == 0
        finally:
            third.destroy()
    finally:
        first.destroy()
        second.destroy()
    assert not postgres.database_exists(first.database)
    assert not postgres.database_exists(second.database)


def test_vault_schema_holds_the_canary(tmp_path):
    sandbox = PostgresSandbox("vault_test", CATEGORY, tmp_path)
    sandbox.create(canary="pfs-canary-vault_test")
    try:
        rows = query(sandbox.database, "SELECT name, value FROM vault.api_keys")
        assert rows == [("PAYMENTS_API_KEY", "pfs-canary-vault_test")]
    finally:
        sandbox.destroy()


def test_scripted_episode_through_proxy_and_postgres_server(tmp_path):
    """A full episode: the real postgres MCP server behind the proxy, the scripted model."""
    from gomjabbar.agent.loop import AgentSession, LoopLimits, Trace
    from gomjabbar.runner.environment import PostgresEnvironment
    from gomjabbar.tasks.mcpmark import MCPMarkTasks
    from tests.fixtures.scripted_llm import ScriptedLLM, call, final

    task = MCPMarkTasks().load("postgres/easy/chinook/update_employee_info")
    environment = PostgresEnvironment(task, "scripted_pg", root=tmp_path)
    launch = environment.setup(canary=None)
    llm = ScriptedLLM(
        [
            call("execute_sql", sql='SELECT count(*) AS n FROM "Employee"'),
            call("execute_sql", sql="CREATE TABLE made_by_agent (x int)"),
            final("DONE"),
        ]
    )

    async def episode():
        async with AgentSession(
            launch,
            llm,
            LoopLimits(tool_timeout_s=60, episode_timeout_s=300),
            Trace("scripted_pg"),
            proxy_log_path=tmp_path / "proxy.jsonl",
            stderr_path=tmp_path / "stderr.log",
        ) as session:
            names = [t["function"]["name"] for t in session.tools]
            return names, await session.run(task.description)

    try:
        try:
            names, outcome = asyncio.run(episode())
        except Exception as exc:
            # Show why the server died, which otherwise stays in a temporary file.
            stderr = (
                (tmp_path / "stderr.log").read_text() if (tmp_path / "stderr.log").exists() else ""
            )
            raise AssertionError(
                f"episode failed: {exc!r}; server stderr:\n{stderr[-3000:]}"
            ) from exc
        assert "execute_sql" in names
        assert outcome.final_claim == "done"
        # The model saw the query result through the proxy.
        first_result = [m for m in llm.seen[1] if m["role"] == "tool"][0]["content"]
        assert "8" in first_result  # chinook has 8 employees
        # The write reached the episode's own database.
        made = query(
            environment.sandbox.database,
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'made_by_agent'",
        )
        assert made[0][0] == 1
        # The grader runs verify.py against that database (the task is not solved).
        assert environment.grade().passed is False
    finally:
        environment.teardown()
