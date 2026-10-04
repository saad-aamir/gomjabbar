# Draft bug report for MCPMark: case-sensitive "ERROR" check misses failed pg_restore

Status: draft, not filed. Saad will post it. This file is a convenience, not part of Prüfstand's own code.

Repo: https://github.com/eval-sys/mcpmark
Found against commit `cd45b7f57923b9b3985467f5139927575f83141c` (the commit Prüfstand vendors), file `src/mcp_services/postgres/postgres_state_manager.py`, in `_create_database_from_backup` (the `pg_restore` block near line 189).

## Title

Postgres state restore can silently accept a failed `pg_restore` (case-sensitive "ERROR" check)

## What happens

When MCPMark restores a Postgres sample database, it runs `pg_restore` and inspects the result:

```python
if result.returncode != 0 and "ERROR" in result.stderr:
    logger.warning(f"pg_restore had errors for {db_name}: {result.stderr}")
else:
    logger.info(f"{db_name} database restored successfully")
```

The check looks for the exact upper-case substring `"ERROR"`. But `pg_restore` writes its diagnostics in lower case, prefixed `pg_restore: error:` (and `pg_restore: warning:`). PostgreSQL's client programs have used lower-case `error:` / `warning:` message prefixes since PostgreSQL 12 (the message style change in 2019). So `"ERROR" in result.stderr` is almost always `False`, the `else` branch runs, and the code logs `"<db> database restored successfully"` even when the restore failed and the database is empty or partial.

Two further points make it easy to miss:

- The `subprocess.run(...)` call has no `check=True`, so a non-zero exit code does not raise.
- `-v` (verbose) output and any genuine upper-case `ERROR:` from the server backend can appear, but the common `pg_restore` driver errors (version mismatch, missing file, connection refused) are the lower-case `pg_restore: error:` lines, which the check does not catch.

Impact: a task can run against a database that was never correctly restored. The agent then fails for the wrong reason, and the run looks healthy in the logs.

## How to reproduce

The clearest trigger is a version mismatch, which is also how we hit it: the published sample backups are PostgreSQL 17 custom-format dumps (`pg_dump` format 1.16), and a PostgreSQL 16 (or older) `pg_restore` refuses them.

1. On a machine whose `pg_restore` is PostgreSQL 16 or older, point MCPMark at a running PostgreSQL and let it restore any sample database (for example `employees`).
2. `pg_restore` fails with, on stderr:
   ```
   pg_restore: error: unsupported version (1.16) in file header
   ```
   and exits non-zero.
3. MCPMark logs `employees database restored successfully` and carries on. The `employees` database exists but is empty.

A smaller standalone reproduction, no MCPMark needed:

```bash
createdb repro
# Any file that is not a valid archive will do.
echo 'not a dump' > bad.backup
pg_restore -d repro -v bad.backup; echo "exit=$?"
# stderr: "pg_restore: error: ..." ; exit is non-zero, but "ERROR" (upper case) is absent.
```

## One-line fix

Compare case-insensitively:

```python
if result.returncode != 0 and "error" in result.stderr.lower():
    logger.warning(f"pg_restore had errors for {db_name}: {result.stderr}")
else:
    logger.info(f"{db_name} database restored successfully")
```

Even simpler and stricter, treat any non-zero exit as a failure (recommended, since `pg_restore` returns non-zero precisely when it could not complete):

```python
if result.returncode != 0:
    logger.warning(f"pg_restore failed for {db_name} (exit {result.returncode}): {result.stderr}")
else:
    logger.info(f"{db_name} database restored successfully")
```

Optionally also assert the restore produced tables (what Prüfstand does): after the restore, count rows in `information_schema.tables` for the target schema and fail if it is zero.

## How Prüfstand works around it

Prüfstand restores the same backups inside MCPMark's own PostgreSQL 17 Docker image (`pgvector/pgvector:0.8.0-pg17-bookworm`), so the version mismatch does not arise, and its own restore check ignores case and requires at least one table before accepting a template (`src/pruefstand/sandbox/postgres.py`; `docs/notes/mcpmark-interface.md`).
