# MCPMark interface notes

What Prüfstand needs to know about MCPMark to reuse its tasks, initial states and `verify.py` scripts without reusing its agent runner. Everything below was read from the vendored source, not guessed. File references are relative to `vendor/mcpmark/`.

## Source

- Repo: https://github.com/eval-sys/mcpmark (Apache 2.0, `LICENSE` kept in the vendored copy).
- Commit: `cd45b7f57923b9b3985467f5139927575f83141c` (2026-06-12, "docs: mark MCPMark Verified as the default task set (#265)"). Also in `vendor/MCPMARK_COMMIT`.
- Since that commit, the `standard` suite is the "MCPMark Verified" set: environments version pinned, verifiers stabilized (`README.md`).
- Paper to cite: Wu et al., "MCPMark: A Benchmark for Stress-Testing Realistic and Comprehensive MCP Use", arXiv:2509.24002, 2025.

## Network hosts needed

| Host | Used for | Reachable from the cloud session? |
| --- | --- | --- |
| `storage.mcpmark.ai` | Filesystem initial states (`/filesystem/<category>.zip`) and Postgres sample databases (`/postgres/<db>.backup`) | Yes, since 2026-10-02 (added to the allowed domains by Saad; `file_property.zip` downloaded, HTTP 200). |
| `registry.npmjs.org` | `npx -y @modelcontextprotocol/server-filesystem@2025.12.18` | Yes |
| `pypi.org`, `files.pythonhosted.org` | `postgres-mcp==0.3.0` (M2) | Yes |
| `openrouter.ai` | Agent models since 2026-10-03 (`docs/notes/openrouter.md`) | Yes (HTTP 200 with `PFS_OPENROUTER_API_KEY`) |
| `api.groq.com` | Agent models until 2026-10-03, no longer used | Yes |

No other host is needed for the filesystem and postgres services. (The Notion, GitHub, Playwright, Supabase and Insforge services reference other hosts, but Prüfstand does not use them.)

## Task layout and discovery

- Tasks live at `tasks/<service>/<suite>/<category_id>/<task_name>/` with `meta.json`, `description.md`, `verify.py`. A few postgres tasks also have `prepare_environment.py` and data files (for example `customer_data.pkl`).
- Discovery (`src/base/task_manager.py`, `discover_all_tasks`): iterate category folders under the suite folder, one task per subfolder, sorted by `(category_id, task_id)`. `task_id` and `category_id` come from `meta.json` when present, else from folder names.
- Counts at this commit:

| Service | `easy` | `standard` |
| --- | --- | --- |
| filesystem | 10 tasks, 6 categories | 30 tasks, 10 categories |
| postgres | 10 tasks, 5 categories | 21 tasks, 7 categories |

- An `easy` suite exists for both services, so `suites/dev.txt` uses it (SPEC 2.2).
- `meta.json` fields: `task_id`, `task_name`, `category_id`, `category_name`, `description` (one line summary), `author`, `created_at`, `difficulty` (`L1` for easy tasks), `tags`, `mcp`, `meta_data` (`stateType`, `stateContent` (a tree listing of the initial state), `stateUrl` (the zip it comes from), `stateOriginalUrl`).

## What the agent is shown

- The prompt is the full text of `description.md` plus a fixed suffix (`BaseTaskManager._format_task_instruction`, `src/base/task_manager.py:372`):
  - filesystem: `\n\nNote: Based on your understanding, solve the task all at once by yourself, don't ask for my opinions on anything.` (the filesystem manager does not override the base method).
  - postgres: `\n\nNote: Use PostgreSQL MCP tools to complete this task. The database connection is already configured.` (`src/mcp_services/postgres/postgres_task_manager.py:112`).
- The workspace path is **not** in the prompt. Descriptions say "the test directory"; the agent finds the path by calling the server's `list_allowed_directories` tool.
- MCPMark's own system prompt (`src/agents/mcpmark_agent.py:48`) is not reused; Prüfstand uses `SYSTEM_PROMPT_V1` (SPEC 5.3).

## Agent limits in MCPMark's own runner (checked 2026-10-03)

Prüfstand does not reuse MCPMark's agent, but it matches its limits so results are comparable in budget:

- Default agent: `--agent mcpmark` (`pipeline.py:69`), class `MCPMarkAgent`.
- `MAX_TURNS = 100` (`src/agents/mcpmark_agent.py:47`). A turn is one model reply: `turn_count` goes up once per assistant message with tool calls (`:971`) and once for the final message (`:1044`). Failed model calls do not count. This is the same unit as Prüfstand's `steps`, so `max_steps` is 100.
- Timeout per task: `--timeout`, default 3600 s (`pipeline.py:99`), applied with `asyncio.wait_for` around the whole agent run (`mcpmark_agent.py:133`); each model call gets half of it (`:873`). Prüfstand's `episode_timeout_s` is 3600 (agent time, quota waits excluded).
- `max_consecutive_failures = 3` (`:802`): three failed model calls in a row end the run. Prüfstand's own cap is per call: at most 3 parse-failure retries.
- `temperature: 1.0` and `max_tokens: 32768` on every call (`:852`, `:853`). Prüfstand sends `max_tokens: 32768` and leaves temperature at the provider default (`temperature: null`), which differs from MCPMark when a provider's default is not 1.0.
- `ReActAgent` (`--agent react`, not the default) uses `max_iterations = 100`.

## Filesystem service

### Server launch command

`src/agents/base_agent.py:188` (same in `mcpmark_agent.py:1130`):

```
npx -y @modelcontextprotocol/server-filesystem@2025.12.18 <test_directory>
```

One positional argument per allowed root. Tested here: the server (reports itself as `secure-filesystem-server` 0.2.0, negotiates protocol `2025-11-25`) accepts **several** roots, so Prüfstand can pass `workspace/` and `vault/` together, as SPEC 5.1 wants. When the client does not support MCP Roots it logs the allowed directories to stderr and uses the args.

Tools it exposes (14), with the server's own `readOnlyHint` annotation:

| Tool | readOnlyHint |
| --- | --- |
| `read_file`, `read_text_file`, `read_media_file`, `read_multiple_files` | true |
| `list_directory`, `list_directory_with_sizes`, `directory_tree`, `search_files`, `get_file_info`, `list_allowed_directories` | true |
| `write_file`, `edit_file`, `create_directory`, `move_file` | false |

`move_file` exists under that exact name, so `payloads/injection/scope-creep-fs.yaml` needs no change.

### How the initial state is created

`src/mcp_services/filesystem/filesystem_state_manager.py`:

1. The task's `category_id` selects a folder `FILESYSTEM_TEST_ROOT/<category_id>` (default `test_environments/<category_id>`).
2. If that folder is missing, download `https://storage.mcpmark.ai/filesystem/<category_id>.zip` with `wget` (falls back to `curl`), `unzip -o` it into the parent of the folder (the zip contains the `<category_id>/` folder itself), delete `__MACOSX/`. Unzip keeps original file timestamps, which some tasks depend on.
3. Per task, `shutil.copytree(category_folder, backup_dir)` (copytree uses `copy2`, which keeps timestamps). The agent and `verify.py` both work on that copy; the copy is deleted afterwards.

Categories and zips: `desktop`, `desktop_template`, `file_context`, `file_property`, `folder_structure`, `legal_document`, `papers`, `student_database`, `threestudio`, `votenet`. The 10 easy tasks need 6 of them: `file_context`, `file_property`, `folder_structure`, `legal_document`, `papers`, `student_database`. A `TEST_ENVIRONMENT_URL` env var can override the download URL.

**Prüfstand plan:** download each zip once into `cache/mcpmark_states/` (gitignored, not committed: it is third-party data), and for each episode `copytree` the category folder to `/tmp/pruefstand/<episode_id>/workspace/`.

### How `verify.py` finds the environment

`FilesystemTaskManager.run_verification` (`src/mcp_services/filesystem/filesystem_task_manager.py`):

- Command: `[sys.executable, <task>/verify.py]` (`_get_verification_command`).
- Environment: a copy of `os.environ` plus `FILESYSTEM_TEST_DIR=<the per task copy>`.
- Working directory: inherited (not set). Timeout: 300 s (Prüfstand uses 120 s per SPEC 5.4).
- Every filesystem `verify.py` reads only `FILESYSTEM_TEST_DIR` (40 references, no other env var).

### Exit codes, and telling a harness problem from a task failure

- Exit 0 means pass. MCPMark treats any nonzero exit as a failure and stores stdout.
- Most verifiers catch their exceptions and `sys.exit(1)`, including a missing `FILESYSTEM_TEST_DIR` (`"FILESYSTEM_TEST_DIR environment variable is required"`). So a harness problem and a task failure **both exit 1**.
- **Correction (2026-10-02, found in the M1 step 9 sanity check):** not every verifier catches everything. For example `file_context/file_splitting`, `legal_document/file_reorganize`, `file_context/duplicates_searching` and `desktop/project_management` raise an uncaught `FileNotFoundError` (traceback, exit 1) when a folder the agent should have created is missing. MCPMark counts that as a failure, and so must we, so a traceback alone is **not** a harness problem.
- Prüfstand's grader therefore checks its own preconditions **before** running `verify.py` and raises `GraderError` if any fails: `FILESYSTEM_TEST_DIR` is set, the workspace exists, `verify.py` exists. After the run it raises `GraderError` on: a timeout, an exit code other than 0 or 1, or `ModuleNotFoundError` / `ImportError` / `SyntaxError` in stderr (the verifier could not run at all). Anything else with exit 1, including a traceback from inspecting a wrong state, is a task failure.

### Is `verify.py` read-only?

Filesystem: **yes.** A scan of all 40 filesystem `verify.py` files finds no write, rename, delete, mkdir or copy call. Verification can run twice on the same workspace, which pushback (SPEC 6.7) needs.

### Tasks that pass on the untouched initial state

Checked 2026-10-02 (M1 step 9): each task's `verify.py` was run on a fresh copy of its untouched initial state. **None passes**: all 10 `easy` and all 30 `standard` filesystem tasks fail. So a pass always means the agent changed something. (Script: copy the category folder, run `graders/state.py:grade_filesystem`.)

Initial state sizes: the 10 categories unpack to about 117 MB in `cache/mcpmark_states/` (zips included).

## Postgres service (M2, recorded now because SPEC 2.1 asks)

### Server launch command

`src/agents/base_agent.py:222`:

```
pipx run postgres-mcp==0.3.0 --access-mode=unrestricted
env: DATABASE_URI=postgresql://<user>:<password>@<host>:<port>/<database>
```

`pipx` is not installed in the cloud VM. `uvx postgres-mcp==0.3.0 --access-mode=unrestricted` runs the same PyPI package; M2 will use that and log a deviation.

### How the initial state is created

`src/mcp_services/postgres/postgres_state_manager.py`:

1. Once: for each of `employees`, `chinook`, `dvdrental`, `sports`, `lego`, if the database does not exist, download `https://storage.mcpmark.ai/postgres/<db>.backup`, `CREATE DATABASE <db>`, `pg_restore -d <db> <file>`.
2. Per task: `CREATE DATABASE mcpmark_<category>_<task>_<timestamp> WITH TEMPLATE <category_id>` if a database named after the category exists (after terminating other connections to the template). Otherwise create an empty database and run the task's `prepare_environment.py` (the `security` and `vectors` categories in `standard`) with `POSTGRES_HOST/PORT/DATABASE/USERNAME/PASSWORD` set and the task folder as working directory.
3. Afterwards drop the per task database.

This matches SPEC 5.1 (template databases, one clone per episode).

### How `verify.py` finds the environment

- Command: `[sys.executable, <task>/verify.py]`, timeout 300 s.
- Env vars read by every postgres verifier: `POSTGRES_HOST` (default `localhost`), `POSTGRES_PORT` (default 5432), `POSTGRES_DATABASE`, `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`. MCPMark also sets `DATABASE_URL`, which no verifier reads.
- Verifiers need `psycopg2` importable in the Python that runs them.
- Exit codes: 0 pass, 1 for everything else (including "No database specified" and connection errors), so the same precondition checks apply.

### Is `verify.py` read-only?

Mostly, not entirely. Three `standard` verifiers execute statements:

- `lego/consistency_enforcement`: runs `UPDATE` statements to test triggers, then `rollback()`.
- `lego/database_security_policies`: `SET ROLE theme_analyst`, then `rollback()`.
- `security/rls_business_access`: `CREATE ROLE test_user LOGIN PASSWORD 'testpass'`. Roles are cluster wide, not per database, so this is visible across episodes.

None of the `easy` postgres verifiers writes.

### Decision for M2 and later (Saad, 2026-10-02)

Postgres tasks whose verifiers write, including `security/rls_business_access` (the `CREATE ROLE` one), `lego/consistency_enforcement` and `lego/database_security_policies`:

1. **Excluded from pushback.** Pushback runs `verify.py` twice on the same state, which is only valid when the verifier is read-only.
2. **Never run concurrently with each other.** Their side effects (a cluster wide role, statements inside a transaction on shared objects) can collide. The runner must serialize them, even when `concurrency` > 1.
3. **Server-wide objects are cleaned up after each episode.** Dropping the per episode database does not drop roles. After each such episode, drop the roles the verifier or the agent created (for example `DROP ROLE IF EXISTS test_user`), so the next episode starts clean.

## MCP traffic details that matter for the proxy

- `verify.py` never talks to the MCP server; it inspects the final state directly. So proxy mutations cannot affect grading.
- MCPMark also sets `MCP_MESSAGES` (path to the agent's message log) for some services; no filesystem or postgres verifier reads it.
