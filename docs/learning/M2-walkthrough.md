# M2 walkthrough: stress

## What was built

Prüfstand now runs MCPMark's postgres tasks as well as its filesystem tasks: each postgres episode gets its own database, cloned from a template inside MCPMark's own PostgreSQL 17 Docker image, and is graded by MCPMark's `verify.py`. The chaos proxy can break the second tool call of an episode in eight ways (latency, timeout, rpc_error, tool_error, malformed, empty, rate_limit, partial) while the real server still executes it. The redteam model writes three paraphrases per task, each checked by a deterministic literal check and an LLM equivalence check, and caches them so every model sees the same text. Two new conditions use these (paraphrase, fault), and the analysis adds robustness drop, fault recovery and false-success rate with task-level bootstrap intervals, shown in a self-contained `report.html`. Before M2 proper, the M1 rules changed (empty replies re-sampled, temperature 1.0, a hard limit on the key's total spend, 401 handled like missing credit) and the filesystem baseline was re-run under them as the baseline of record.

## File map

Source (`src/pruefstand/`), new or changed in M2:

- `agent/loop.py`: empty-reply re-sampling (`_ask_model`, `is_empty_reply`, `empty_reply_kind`), accounting split into `_account`.
- `agent/llm.py`: 401 and missing credit become an "account" pause (`QuotaExhausted.daily = False`).
- `sandbox/postgres.py`: the Docker container, template restore, per-episode clone, vault schema, server command.
- `graders/state.py`: `grade_postgres`, with a precondition check on the episode database.
- `runner/environment.py`: `PostgresEnvironment` next to `FilesystemEnvironment`.
- `proxy/mutators.py`: one small function per fault profile (`apply_fault`).
- `proxy/relay.py`: picks the fault rule on the request, applies it on the response, honours delays and swallowed lines.
- `redteam/literals.py`: the literal check (regexes for code spans, quotes, paths, files, numbers, identifiers, table cells).
- `redteam/paraphrase.py`: generation with both checks, the cache, the samples page.
- `conditions/paraphrase.py`, `conditions/fault.py`: the two new conditions; `conditions/__init__.py` gains `prompt_for`.
- `runner/episode.py`: the prompt comes from the condition; new result fields for empty replies.
- `runner/budget.py`, `runner/grid.py`: `KeySpendGuard` (the key's absolute limit), account pauses not stored for the day.
- `analysis/metrics.py`: robustness drop, fault recovery, false success per task, MCPMark rule, empty-reply rate.
- `report/card.py`, `report/svg.py`, `report/html.py`, `report/templates/report.html.j2`: the report card and the HTML page.
- `cli.py`: `paraphrase` command, `report` writes HTML, key guard wiring, doctor checks the container.

Tests, data and docs:

- `tests/unit/test_mutators.py`: the exact bytes the client gets for every profile.
- `tests/integration/test_agent_loop.py`: every profile end to end (the server executed the call), empty-reply re-sampling.
- `tests/integration/test_postgres.py`: isolation of concurrent episodes, the vault, a scripted episode through the real postgres server.
- `tests/unit/test_paraphrase.py`, `test_report.py`, plus additions to `test_llm.py`, `test_budget.py`, `test_runner.py`, `test_analysis.py`.
- `suites/dev.txt`: the 10 postgres easy tasks.
- `cache/paraphrases/`: 54 accepted paraphrases for the 20 dev tasks, with every rejection and drop.
- `docs/notes/empty-replies.md`, `paraphrase-samples.md`; additions to `mcpmark-interface.md` and `mcp-stdio.md`.
- `configs/*.yaml`: temperature 1.0, `key_spend_cap_usd: 4.5`.
- `runs/dev-20261003-052646/`: the dev run of record (baseline, paraphrase, fault).

## Three important design decisions

1. **Faults are decided on the request and applied to the response.** The relay counts `tools/call` requests as they pass from client to server; when the nth matching call goes by, it remembers that request id. The request is always forwarded, so the real server executes the call; only the answer coming back is replaced, delayed or swallowed. This matches SPEC 5.2 ("the real server does execute the call") and keeps state effects realistic: a timeout does not mean nothing happened. Rejected: answering faulted calls from the proxy without forwarding them, which is simpler but tests a world where errors never have side effects.

2. **Paraphrases are made once, checked twice, cached and committed.** A separate command generates them; runs only read the cache and refuse to start without it. The literal check is deterministic and runs first, so the cheap check filters most bad candidates before the LLM is asked. Every model therefore sees byte-identical prompts, and the rejected candidates are kept as evidence. Rejected: generating paraphrases on the fly per episode, which would give each model different text and mix wording effects with model effects.

3. **Postgres runs in MCPMark's own image, one template per sample database, one clone per episode.** The backups are PostgreSQL 17 dumps, which the VM's native PostgreSQL 16 cannot read, so the container uses MCPMark's `pgvector/pgvector:0.8.0-pg17-bookworm`. `CREATE DATABASE ... TEMPLATE` gives each episode a full private copy in about a second, so two concurrent episodes of the same task cannot see each other's writes (tested), and dropping it afterwards cleans up everything inside the database. Rejected: the native PostgreSQL 16 (cannot restore the data) and restoring the dump per episode (seconds to minutes each).

## What is fragile

- **Shared, cluster-wide objects.** Roles are not per database. Easy tasks do not create lasting roles in a way that collides, but some standard postgres verifiers do (`CREATE ROLE test_user`); the serialization and cleanup decided for them are not built yet.
- **The empty-reply classification is a heuristic.** Four characters per token is rough; the two kinds are approximate.
- **Paraphrase generation depends on the model obeying "digits stay digits".** Prompt v1 lost a whole task to "five"; v2 lists the literals, but some variants were still dropped for four postgres tasks.
- **Fault timing.** `timeout` and `malformed` both cost `tool_timeout_s` (30 s) of agent time each; with long tasks the fault condition is the slowest.
- **The postgres server's dependencies.** `postgres-mcp` 0.3.0 does not pin its dependencies; only the MCP SDK is pinned (to 1.30.0). Another library release could break it the same way mcp 2.x did.
- **The Docker daemon.** It is not running at session start; the session hook starts it. If it fails, every postgres episode fails before setup and the run stops.
- **The key spend guard reads a number OpenRouter may update with a delay**, which is why it keeps a reserve per running episode and stops at 4.50 USD under a 5 USD key limit.

## Ten interview questions

1. Why does the proxy decide a fault when it sees the request, but apply it to the response?
2. In which fault profiles can the agent's final state still be correct even though it saw an error, and why?
3. How does the literal check avoid counting list numbering as a number of the task, and why did that bug let "three" pass for 3?
4. Why do paraphrases go through a deterministic check before the LLM equivalence check?
5. How do you prove two concurrent postgres episodes of the same task cannot see each other's writes?
6. Why does a template database plus `CREATE DATABASE ... TEMPLATE` beat restoring a dump per episode?
7. Why is the robustness drop computed per task before bootstrapping, instead of subtracting two overall pass rates?
8. What is the "MCPMark rule" score, and why report it next to the normal pass rates?
9. Why are transport failures excluded from fault recovery, and where do they show up instead?
10. How does the key spend guard keep total spend under 4.50 USD across runs, and why does it fail closed?
