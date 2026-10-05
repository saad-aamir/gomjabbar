# M1 walkthrough: the bench works

## What was built

Gom Jabbar can now take an MCPMark filesystem task, copy its initial state into a fresh sandbox, start the official filesystem MCP server behind a byte-faithful proxy, and let a model solve the task through its own agent loop. The final state is graded by MCPMark's own `verify.py`, and the result row (state pass, strict pass, honesty, tokens, cost, provider, config hash, git commit) is written only after grading succeeded. A runner expands a config into a deterministic list of episodes, resumes after a crash by skipping finished episode ids, throttles itself to provider limits, stops before a spend cap, and commits results to git in checkpoints. The analysis computes pass@1 and pass^k with 95% intervals by resampling tasks, and the CLI offers `doctor`, `pilot`, `estimate`, `run` and a terminal `report`. The M1 baseline ran 10 filesystem tasks x 5 attempts x 2 models (gpt-oss-20b and gpt-oss-120b on OpenRouter, pinned to CoreWeave).

## File map

Source (`src/gomjabbar/`):

- `models.py`: Pydantic data contracts (Task, EpisodeSpec with its deterministic id, TraceEvent, EpisodeResult).
- `config.py`: RunConfig and ModelConfig, YAML loading, config hash, spend-cap safety checks.
- `paths.py`: repo and vendored MCPMark locations.
- `tasks/base.py`, `tasks/mcpmark.py`: task loader protocol and the MCPMark adapter (description plus MCPMark's prompt suffix, suite files).
- `sandbox/filesystem.py`: downloads each category zip once, copies it per episode, tears it down.
- `sandbox/canary.py`: per-episode fake secret for the vault conditions (used from M2).
- `proxy/__main__.py`, `proxy/relay.py`, `proxy/plan.py`: the stdio relay between agent and server, request-id tracking, side log; the plan models (empty in M1).
- `agent/llm.py`: one model call through LiteLLM: provider pinning, retries and error classification, parse-failure cap, tokens, cached tokens, cost.
- `agent/loop.py`: the MCP client session and the agent loop: tools, steps, timeouts, trace, final claim, malformed tool names.
- `agent/prompts.py`: `SYSTEM_PROMPT_V1`.
- `graders/state.py`: runs `verify.py`, separates harness problems (GraderError) from task failures.
- `graders/honesty.py`, `graders/policy.py`: false success; canary access and leak.
- `conditions/__init__.py`, `conditions/baseline.py`: expands conditions into specs and proxy plans (baseline only in M1).
- `runner/grid.py`: builds and orders specs, runs them with concurrency, resume, quota and budget checks, checkpoints.
- `runner/episode.py`: one episode end to end, grade first, write after.
- `runner/environment.py`: glue between an episode and its sandbox, server launch and grader.
- `runner/quota.py`: request and token throttles, daily counters in `quota.json`.
- `runner/budget.py`: spend-cap guard.
- `runner/store.py`: run folder layout, append-only results, traces, notable traces.
- `runner/checkpoint.py`: git commit and push of run artifacts.
- `analysis/metrics.py`, `analysis/stats.py`: pass@1, pass^k, task-level bootstrap.
- `analysis/estimate.py`, `analysis/summary.py`: pilot-based projection; terminal report card.
- `cli.py`: the Typer commands.

Tests, scripts, docs and data:

- `tests/fixtures/fake_server.py`, `scripted_llm.py`, `echo_server.py`: a tiny MCP server, a model stand-in, a relay test target.
- `tests/unit/*`, `tests/integration/*`: 90 tests, no paid calls.
- `scripts/cloud_session_start.sh`: session hook (Postgres, `uv sync`).
- `scripts/rescore_claims.py`: re-scores `final_claim` of a finished run.
- `configs/dev.yaml`, `full.yaml`, `local.yaml`: run configs.
- `suites/dev.txt`: the 10 filesystem `easy` tasks (postgres half in M2).
- `docs/notes/mcpmark-interface.md`, `mcp-stdio.md`, `openrouter.md`: investigation notes.
- `DEVIATIONS.md`: every departure from the spec, dated.
- `runs/pilot-*`, `runs/dev-20261003-033437`: pilots and the M1 baseline (results, config, log, notable traces).

## Three important design decisions

1. **A raw byte relay as proxy, not an MCP server built with the SDK.** The proxy reads one JSON line at a time and writes the same bytes back unless a mutation fires. This keeps the baseline exactly what the real server said (a test checks byte equality both ways) and lets later milestones send deliberately broken messages that an SDK would refuse to produce. Rejected: an SDK-based man-in-the-middle server, which re-serializes every message and can silently change it.

2. **Grade first, write after, and an episode id that does not depend on the run.** The result row is appended only after `verify.py` ran and its outcome was classified; a harness problem raises GraderError and nothing is written. The episode id is a hash of the spec without the run id, so resume is "skip ids already in results.jsonl", and a killed run continues where it stopped (tested, and checked by hand on the M1 run). Rejected: writing a placeholder row at episode start and updating it, which leaves partial rows after a crash.

3. **One pinned upstream provider, and cost from the provider's own number.** OpenRouter would otherwise spread requests over providers with different engines and weight formats, so a difference between attempts could come from the stack. Every request pins `coreweave/fp4` with fallbacks off, and the serving provider is stored on every row so a broken pin is visible. Cost is the `usage.cost` the response reports, because LiteLLM's price table holds a different provider's price. Rejected: OpenRouter's default load balancing (cheaper and more available, but not reproducible) and Groq's free tier (quota allowed only a few episodes per day).

## What is fragile

- **The provider.** If CoreWeave stops serving a model, requests fail with 404 and episodes end as `llm_error`; switching providers makes results incomparable. The OpenRouter key expires 2026-10-10.
- **gpt-oss output through the provider's Harmony parser.** gpt-oss-20b often leaks format tokens into tool names (`write_file<|channel|>commentary`: 93 calls in 20 of 50 baseline episodes), and the provider rejected 9.5% of its replies as unparsable (6 episodes ended as `llm_error` after 3 retries). Both models sometimes end a turn with reasoning only and an empty message, which the loop must treat as the final answer: 21 of the 100 baseline episodes ended that way, all failed. These are measured, not repaired, and part of the effect may belong to the serving stack rather than the model.
- **Error classification by message text.** Retry versus stop decisions match words in provider error messages (`per day`, `unexpected tokens remaining`, `Insufficient credits`). A provider rewording its errors changes behaviour silently.
- **`final_claim` from the first word.** A model that writes "Task completed" instead of DONE scores `none`, which lowers strict pass without changing state pass.
- **Wall time and estimates.** With MCPMark's 100 steps, one hard task (`file_splitting`) took gpt-oss-120b 14 to 17 minutes per attempt. The pilot used only 3 tasks of one category with 40 steps, so its estimate (1.2 h) matched the baseline (about 1 h) partly by luck: easy tasks were faster than the pilot average, the hard one far slower.
- **Concurrent git use.** The run's own checkpoints and manual commits share one working tree; a commit at the wrong moment can fail on the index lock (the run logs it and retries at the next checkpoint).
- **`quota.json` and episode costs of interrupted episodes** are not part of any result row, so the spend guard slightly undercounts after crashes.

## Ten interview questions

1. Why does the proxy forward raw bytes instead of parsing and re-serializing MCP messages, and how do you prove it changes nothing?
2. How does resume work, and why is the run id left out of the episode id?
3. What is the difference between pass@1 and pass^k, and how is pass^k estimated from n attempts with c passes?
4. Why do the confidence intervals resample tasks instead of episodes?
5. How does the grader tell a harness problem from a task failure when both exit with code 1?
6. Why is a result row only written after grading, and what happens to an episode interrupted by a quota pause?
7. Why pin one OpenRouter provider, and what would you see in the data if the pin failed?
8. How do you keep retries from distorting quota counts and cost, and why are parse-failure retries capped and counted?
9. What are state pass and strict pass, and when do they disagree?
10. The pilot ran 3 tasks with a 40-step limit and still predicted the 100-step baseline's time well. Why was that partly luck, and how would you make the estimate more robust?
