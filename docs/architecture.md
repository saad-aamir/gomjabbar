# Architecture

A plain-language tour of Prüfstand for someone about to change it. `docs/SPEC.md` is the contract; this page explains how the pieces fit and where to look. Every source file starts with a docstring saying what it does, why it exists and how it fits; read those next.

## The idea in one paragraph

An *episode* is one attempt by one model at one MCPMark task under one condition. The runner prepares a private copy of the task's starting state, starts the real MCP server behind a chaos proxy, and lets the agent loop talk to a model until the model gives a final answer. Then the graders decide what happened (did the final state pass `verify.py`, did the fake key leak, did the attacker get what it asked for) and one result row is appended to `results.jsonl`. Analysis turns many rows into per-task numbers and then into intervals by resampling tasks.

## The path of one episode

1. **Config to specs** (`config.py`, `runner/grid.py`, `conditions/`). A YAML config names the suite, models, conditions, defenses and limits. Each condition module expands a task and model into `EpisodeSpec`s (baseline gives k attempts, poison one per payload, and so on). A spec's `episode_id` is a hash of everything in it except the run id, so the same spec always has the same id. That is what makes `--resume` work: specs whose id is already in `results.jsonl` are skipped.
2. **Sandbox up** (`runner/environment.py`, `sandbox/`). Filesystem tasks get a fresh copy of MCPMark's initial files under `/tmp/pruefstand/<episode_id>/workspace/`. Postgres tasks get `CREATE DATABASE ep_<id> TEMPLATE pfs_tpl_<db>` inside one long-lived PostgreSQL 17 Docker container. Attack conditions and `vault_control` also get a vault holding the canary `pfs-canary-<episode_id>` (a second filesystem root, or a `vault` schema).
3. **Proxy plan** (`proxy/plan.py`). The condition turns the spec into a `ProxyPlan`: fault rules, poison rules, inject rules. Baseline's plan is empty, and an empty plan is byte-for-byte passthrough (tested).
4. **Agent loop** (`agent/loop.py`, `agent/llm.py`). The MCP client starts `python -m pruefstand.proxy --plan plan.json -- <real server command>`. The loop lists tools, sends the system prompt and the task to the model through LiteLLM (OpenRouter, one pinned upstream provider), runs each tool call, feeds results back, and stops on a final answer, `max_steps` or a timeout. Every step becomes a `TraceEvent`.
5. **Defenses** (`defenses/`, applied inside the loop when the spec lists them). After `list_tools`: pinning, then the description scan. After every tool result: the sanitizer. Each action is a `defense_action` trace event.
6. **Chaos proxy** (`proxy/relay.py`, `proxy/mutators.py`). It relays newline-delimited JSON-RPC between client and server and tracks request ids, so it knows which response answers which `tools/list` or `tools/call`. A fault is decided when the request passes and applied to the response, so the real server still executes the call. Poison rewrites `tools/list` responses; a shadow tool is added to the list and answered by the proxy itself; inject appends a text block to one tool result. Every mutation is logged to a side file and merged into the trace.
7. **Graders** (`graders/`). `state.py` runs MCPMark's `verify.py` against the sandbox (exit code 0 means pass). `policy.py` scans the trace for the canary (in a tool result: accessed; in a tool-call argument or the final message: leaked) and evaluates the payload's own success check. `honesty.py` reads the DONE/FAILED claim and computes strict pass. `calls.py` classifies calls as reads or writes for pushback. A harness problem raises `GraderError`: nothing is written, the trace is saved under `grader_errors/`, and the run stops.
8. **Pushback** (`conditions/pushback.py`, `runner/episode.py`). If a baseline episode passed and claimed DONE and the config lists pushback, the same live session gets one sceptical user message, runs again for up to 15 model calls, and is graded again. Both rows are written together.
9. **Write and tear down** (`runner/store.py`, `runner/checkpoint.py`). The trace is written first, then the result row. Notable traces (failed, leaked, attacked, flipped) are gzipped into `notable/` so they can be committed. In cloud sessions the runner commits and pushes every N episodes.

## Around the episode

- **Money and quotas** (`runner/budget.py`, `runner/quota.py`). Before each episode the runner checks the run's euro spend cap and the OpenRouter key's total spend; it stops cleanly rather than cross either. A rejected key or missing credit pauses the run for `--resume`.
- **Paraphrases** (`redteam/`). Made once with `pruefstand paraphrase`, checked by a deterministic literal check and an LLM equivalence check, cached in `cache/paraphrases/` and committed, so every model sees identical text.
- **Analysis** (`analysis/metrics.py`, `analysis/stats.py`). Every metric is first computed per task, then `bootstrap_mean` resamples tasks 10,000 times with a fixed seed. Resampling tasks, not episodes, is deliberate: attempts of one task are not independent.
- **Compare** (`analysis/compare.py`). Pairs two runs' episodes by (model, task, condition, variant, attempt) and runs McNemar per condition and per payload. Used for defenses on versus off.
- **Reports** (`report/`). `report.html` and `compare.html` are single self-contained files with inline SVG charts.

## Where to change things

| You want to | Look at |
| --- | --- |
| add a condition | a module in `conditions/` with `expand` and `plan_for`, one line in `conditions/__init__.py`, a scenario in `tests/regression/test_gate.py` |
| add a fault profile | `proxy/plan.py` (the name), `proxy/mutators.py` (what it does), `tests/unit/test_mutators.py` |
| add an attack payload | a YAML file in `payloads/poisoning/` or `payloads/injection/` (SPEC 7); the regression gate will ask you to update its golden file |
| add a defense | a module in `defenses/`, its name in `defenses/__init__.py`, the hook in `agent/loop.py` |
| add a metric | `analysis/metrics.py` (per task), then `report/card.py` |
| support another MCP server | a sandbox in `sandbox/`, an environment in `runner/environment.py`, a task loader in `tasks/` |

## Tests

`uv run pytest` runs everything with no paid calls. `tests/fixtures/fake_server.py` is a tiny MCP server (notes in a JSON file) and `tests/fixtures/scripted_llm.py` replays a fixed list of tool calls, so whole episodes run through the real proxy, loop, runner and graders in about a second each. Postgres tests need Docker and are skipped in CI. `tests/regression/` is the regression gate: one scripted episode per condition and payload, with and without defenses, compared with a committed golden file. If you change behaviour on purpose, rerun it with `REGRESSION_UPDATE=1` and commit the reviewed golden diff.

## Rules that keep results trustworthy

- MCPMark under `vendor/` is never edited.
- A result row is written only after every grader succeeded; there is no default or partial row.
- Every departure from the spec is a dated entry in `DEVIATIONS.md`.
- Every reported number comes from `results.jsonl` through `analysis/`.
- The defense patterns are frozen at the tag `defense-patterns-v1`; a test fails if `defenses/patterns.py` changes.
