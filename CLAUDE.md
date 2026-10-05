# CLAUDE.md

Standing instructions for Claude Code working in this repo. Read this file, then `docs/SPEC.md`, then the current milestone file in `docs/milestones/`.

## What this project is

**Gom Jabbar: Chaos Testing for Tool-Using AI Agents.** An open-source test bench that runs an agent against a real MCP server many times under controlled stress (repeats, reworded tasks, tool faults, poisoned tools, injected tool output, user pushback) and reports the results with confidence intervals along four dimensions:

1. **Reliability:** does the agent do the same thing every time?
2. **Robustness:** how much does it degrade when inputs or tools misbehave?
3. **Security:** does it follow instructions from untrusted tools?
4. **Behavioural stability:** does it abandon correct work under pressure?

The first targets are the public **MCPMark** benchmark tasks for the **Filesystem** and **PostgreSQL** MCP servers. MCPMark supplies the tasks and the `verify.py` scripts that check the final state. Gom Jabbar supplies its own agent loop, a chaos proxy between agent and server, the stress conditions, graders, statistics and reports.

`docs/SPEC.md` is the single source of truth. If anything else (including the PRD) disagrees with it, the spec wins.

## Who reads this code afterwards

The owner, Saad, did not write this code. After each milestone he will read it to learn it well enough to explain every design decision in a job interview. So:

- **Readability beats cleverness.** Prefer plain, explicit code over dense or magical code.
- **Every module starts with a plain-language docstring** with three short parts: what it does, why it exists, how it fits with the rest.
- **Every piece of code carries inline comments explaining what each part does.** Comment the intent, not just the mechanics. This is a hard requirement, not a style preference.
- **No em dashes or en dashes in prose you write** (docs, comments, README). Use commas, colons or full stops.

## How to work

1. **One milestone at a time.** Only work on the milestone named in the prompt. Do not start the next one.
2. **Plan first.** Before writing code for a milestone, produce a short plan (files to create, order, tests) and wait for approval.
3. **Tests first for the core.** The proxy, graders, metrics and stats get unit tests before or alongside the code. Use the fake MCP server and the scripted LLM described in the spec so tests never call a paid API.
4. **Small commits.** One feature per commit, conventional messages (`feat(proxy): add fault injection`). Run `uv run pytest` and `uv run ruff check .` before every commit.
5. **Ambiguity rule.** If the spec is unclear, choose the simplest option that satisfies the acceptance criteria, and add a dated entry to `DEVIATIONS.md` saying what you chose and why. Do not stop to ask about small things.
6. **Stop at the gate.** When a milestone's acceptance criteria pass, stop, write the milestone walkthrough (see below), and report. Do not continue.
7. **Investigate before integrating.** For anything external (MCPMark's task interface, which MCP servers it launches, how `verify.py` finds the environment, how the MCP Python SDK handles bad messages), read the actual source or docs first and write findings to `docs/notes/`. Never guess an external interface.

## Milestone walkthrough (required at every gate)

At the end of each milestone, write `docs/learning/M<n>-walkthrough.md` in plain language:

- What was built, in five sentences or fewer.
- A map of the files added or changed, one line each.
- The three most important design decisions, why they were made, and the alternative that was rejected.
- What is fragile or likely to break.
- Ten questions an interviewer could ask about this milestone, without answers.

## Where you are running

You are most likely in a **Claude Code cloud session**: an Ubuntu 24.04 VM (4 vCPU, 16 GB RAM) that is reclaimed after inactivity. Read `docs/CLOUD.md` once. In short:

- **Commit and push often.** Anything not pushed can vanish when the VM is reclaimed. Push after every passing feature, and use `--checkpoint-every` for runs.
- **Postgres runs in Docker** (MCPMark's PostgreSQL 17 image; the preinstalled PostgreSQL 16 cannot restore MCPMark's backups). The session hook starts `dockerd`; the sandbox starts the `pruefstand-pg` container on first use (the container keeps its pre-rename name, see `DEVIATIONS.md`).
- **No Ollama here.** Cloud runs use API models only. `configs/local.yaml` is for Saad's Mac.
- **The harness uses paid gpt-oss models through OpenRouter, key in `PFS_OPENROUTER_API_KEY`,** pinned to one upstream provider with fallbacks off (`docs/notes/openrouter.md`). Runs stop cleanly before they would cross the config's `spend_cap_eur` (SPEC 5.5). Never read, set or export `ANTHROPIC_API_KEY`.
- **Network is an allowlist.** If something you need is blocked (a download host for MCPMark data, for example), stop and tell Saad the exact host. Do not look for workarounds.
- **Long runs go in the background, in chunks.** Start them with `nohup ... &`, one condition at a time (`--only`), and check progress with `tail` instead of waiting on a single long command.

## Hard rules

- Never modify anything under `vendor/` (creating the vendored copy in the first session is the one exception). MCPMark is a vendored, pinned copy (commit in `vendor/MCPMARK_COMMIT`).
- Never commit secrets. API keys live in `.env`, which is gitignored. Canary secrets used in tests are fake by design.
- Only call paid models in a run whose config has spend_cap_eur above 0, set by Saad. Never raise a spend cap yourself.
- Never run the `full` suite until `PRE_REGISTRATION.md` has `Status: FINAL` at the top. Saad finalizes it, not you.
- Only test MCP servers started locally by this project. No requests to third-party services except the model APIs.
- A grader or judge error must abort the write of that result. Never write a partial or default result.
- Keep `DEVIATIONS.md` honest and dated.

## Commands

```bash
# Install dependencies into the project virtualenv
uv sync

# Run unit and integration tests (no paid API calls)
uv run pytest

# Lint and format
uv run ruff check . && uv run ruff format .

# Check the environment: Node/npx, Postgres, API keys, one test call per model, vendored MCPMark
uv run gomjabbar doctor

# Small paid smoke run (3 tasks)
uv run gomjabbar pilot --config configs/dev.yaml --tasks 3

# Full grid from a config, resumable
uv run gomjabbar run --config configs/dev.yaml

# HTML report for a run
uv run gomjabbar report runs/<run_id>

# Paired comparison of two runs
uv run gomjabbar compare runs/<run_a> runs/<run_b>
```

## Stack

Python 3.11+, uv, MCP Python SDK, LiteLLM, Pydantic v2, Typer, Jinja2, NumPy, pandas, statsmodels, pytest, ruff. Node 20+ for npx-launched MCP servers. Docker for Postgres. Ollama for local models.
