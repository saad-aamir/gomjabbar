# Deviations

Every departure from docs/SPEC.md or PRE_REGISTRATION.md, newest first. One entry per deviation.

Format:

## YYYY-MM-DD: short title
- **What the spec said:**
- **What we did instead:**
- **Why:**
- **Effect on results:** none / which metrics, and how

## 2026-10-04: M4 has no confirmatory full run
- **What the spec said:** M4 step 8: if `PRE_REGISTRATION.md` is FINAL, estimate and run `configs/full.yaml` on the standard suite.
- **What we did instead:** no full run. `PRE_REGISTRATION.md` is rewritten as "Status: PLANNED, NOT RUN" (entry below). `configs/full.yaml` stays as it is, with `spend_cap_eur: 0`, so it refuses to start.
- **Why:** decided by Saad: the budget is fixed and no credit will be added.
- **Effect on results:** every number in this repo is exploratory (20 easy dev tasks). No hypothesis is tested confirmatorily.

## 2026-10-04: PRE_REGISTRATION.md rewritten by Claude, on Saad's authorization
- **What the spec said:** CLAUDE.md and the file itself: Claude Code never edits the hypotheses or thresholds; Saad finalizes the file.
- **What we did instead:** Claude rewrote the file on Saad's explicit instruction: status "PLANNED, NOT RUN", all results labelled exploratory, and six hypotheses H1 to H6 (worded by Saad) proposed for a future confirmatory run on the held-out standard suite with Holm correction, each citing its dev numbers. The old draft's H1 to H4 and E1 to E5 are replaced.
- **Why:** with no full run, the old draft described a study that will not happen. The new hypotheses come from what the dev runs showed, so they are labelled as generated from exploratory data.
- **Effect on results:** none on recorded numbers. The hypotheses are now data-informed, which is why they need fresh data (the standard suite) to be tested.

## 2026-10-04: a third defense, description_scan
- **What the spec said:** SPEC 5.7 and M4 step 1: two defenses, pinning and sanitizer.
- **What we did instead:** a third defense `defenses/description_scan.py` (asked by Saad). After `list_tools` at session start it scans every tool description and every parameter description for instruction-like sentences (the sanitizer's pattern family plus phrases such as "mandatory", "before finishing any task", "include ... in the arguments", "do not mention"), removes matching sentences, logs each removal, and hides a tool from the model entirely when the removals leave its description empty or remove more than half of it. All three defenses share one pattern module. False positives are measured on clean MCPMark tool descriptions and on clean tool results from the committed traces.
- **Why:** pinning only catches descriptions that change mid-session and the sanitizer only looks at tool results, so neither touches a description that is poisoned from the first `tools/list`, which is what the poison condition does.
- **Effect on results:** defense runs can use any of the three names in `defenses`.

## 2026-10-04: pinning cannot fire in the defense experiment
- **What the spec said:** M4 step 3: re-run poison, inject and rug pull with pinning and sanitizer.
- **What we did instead:** pinning is built and unit tested as SPEC 5.7 says, and is switched on in the defense run, but it will log no actions there. It protects against descriptions that change after the first listing; the poison condition serves poisoned descriptions from the first `tools/list`, and the rug pull condition (P1) was never built. Rug pull is not run.
- **Why:** rug pull was out of reach in M3 (see the M3 walkthrough); the defense run measures the defenses against the attacks that exist.
- **Effect on results:** the defense run's effect on poison comes from description_scan, on inject from the sanitizer. Pinning's effectiveness is untested on real models.

## 2026-10-04: defense patterns written with the payloads in view, frozen, then tested on held-out payloads
- **What the spec said:** nothing on how defense patterns are chosen.
- **What we did instead:** the sanitizer and description_scan patterns were written by Claude knowing the 8 repo payloads, so attack reduction on those payloads is an upper bound. To get a fair number, the patterns are frozen once the defenses and the false-positive measurement are done: committed and tagged `defense-patterns-v1` (the tag is recorded in the entry "defense patterns frozen" when it is made). After the tag, no pattern changes for this experiment. `payloads/holdout/` is created empty; Saad writes those payloads after the freeze, and they are never used to tune patterns. The payload loader accepts holdout files as a third set (variants `poison-holdout-<id>` for files with a `mode`, `inject-holdout-<id>` for the rest), chosen with a new config field `payload_set: holdout`. `configs/holdout.yaml` (no defenses) and `configs/holdout-defended.yaml` (all three) run them on gpt-oss-120b, `spend_cap_eur: 0.5` each. Asked by Saad.
- **Why:** a defense tuned on its test set looks better than it is.
- **Effect on results:** two defense numbers: on the repo payloads (upper bound) and on the holdout payloads (fair), reported separately.

## 2026-10-04: the defense experiment runs on gpt-oss-120b only, and Saad runs it
- **What the spec said:** M4 step 3: re-run poison, inject and rug pull with defenses on the dev suite, plus baseline, for the models of the dev config.
- **What we did instead:** `configs/defense.yaml`: gpt-oss-120b only, dev suite, conditions baseline (k=5, no pushback), poison and inject, `defenses: [pinning, sanitizer, description_scan]`, `spend_cap_eur: 1.2` (set by Saad). Claude builds and tests it but does not run it; Saad runs it from his Mac. It is compared with the M3 run `runs/dev-20261003-205231`.
- **Why:** decided by Saad: fixed budget, and gpt-oss-120b is the model the poisoning works on (76.7% attack success against 33.3% for 20b).
- **Effect on results:** no defense numbers for gpt-oss-20b. Estimated cost from M3's averages: about 0.51 EUR.

## 2026-10-04: compare pairs by model too, and reports the baseline change with an interval
- **What the spec said:** SPEC 9: pair episodes by `(task_id, condition, variant_id, attempt)`; McNemar, discordant counts, odds ratio, p-value.
- **What we did instead:** pairs are keyed by `(model, task_id, condition, variant_id, attempt)`, and only models present in both runs are compared (a run with two models has two episodes per SPEC key). The outcome compared is attack success (`attacker_goal_met` or `canary_leaked`) for poison and inject, and state pass for every other condition. Next to McNemar, each condition gets both rates with task-bootstrap intervals and the paired change with a task-bootstrap interval. The odds ratio is b/c with 0.5 added to both counts when either is zero.
- **Why:** without the model in the key, pairs from different models would be matched. The interval on the baseline change is what "utility cost" needs; McNemar only gives a p-value.
- **Effect on results:** none on recorded rows.

## 2026-10-04: EpisodeResult records the number of defense actions
- **What the spec said:** SPEC 4 EpisodeResult has no defense field; defense actions are trace events.
- **What we did instead:** new field `defense_actions: int = 0`, the number of `defense_action` events in the episode (removed lines, removed sentences, hidden tools, pinned changes, blocked calls to hidden tools). Old rows read as 0.
- **Why:** the report and compare can show how often a defense acted without reading traces (most traces are not committed).
- **Effect on results:** none on scores.

## 2026-10-04: README without the headline report image
- **What the spec said:** M4 step 6 lists "the headline report image" in the README.
- **What we did instead:** the README follows Saad's list for M4 (summary, dimensions, quickstart, architecture diagram, results with intervals, confound finding, leak trace demo, defense results placeholder, limitations, cost, MCPMark credit, ethics, how this was built). The results are tables, not an image.
- **Why:** Saad's M4 list replaces step 6 and does not include the image; a table with intervals is readable on GitHub without a binary file.
- **Effect on results:** none.

## 2026-10-04: a grader error still saves the episode's trace
- **What the spec said:** a grader or judge error aborts the write of that result (SPEC 5.4, CLAUDE.md); SPEC 4 lists what a run commits (config, results, log, report, notable traces).
- **What we did instead:** when grading raises `GraderError`, the episode runner first saves the episode's trace, with a final `error` event naming the failure, gzipped to `runs/<run_id>/grader_errors/<episode_id>.jsonl.gz`, logs the path in `run.log`, then re-raises. If the error came from the re-grade after pushback, the pushback episode's trace is saved too. Still no result row is written, and the run still stops. `grader_errors/` is added to the folders checkpoints commit.
- **Why:** asked by Saad: without the trace there was nothing to debug a grader failure with, and on a cloud VM the gitignored `traces/` folder is lost when the VM is reclaimed. A separate folder (not `traces/` or `notable/`) keeps a later rerun's trace from being mistaken for the failed one.
- **Effect on results:** none; results.jsonl is unchanged.

## 2026-10-04: verify.py timeout raised from 120 s to 600 s
- **What the spec said:** SPEC 5.4, verify.py timeout 120 s.
- **What we did instead:** 600 s.
- **Why:** postgres/easy/employees/department_summary_view (episode 1f828f54940d9097, gpt-oss-120b) timed out at 120 s during the M3 dev run, which aborted the run with GraderError. The employees database is large, and MCPMark itself allows up to 3,600 s per task. The episode wrote no row and reruns on resume. Its trace was not saved, so slow-view versus lock could not be told apart; a repeat timeout at 600 s would point to a lock.
- **Effect on results:** none on recorded rows; the resumed run continues under a newer commit.

## 2026-10-03: step 5 conditions and the box 2, 3 and 6 tests are written by Saad
- **What the spec said:** M3 steps 3 to 5 build the policy success-check tests, the poison, inject and vault_control conditions, and their tests; nothing says who writes them.
- **What we did instead:** the session's safety classifier repeatedly blocked the model from writing the attack-condition modules (`conditions/poison.py`, `conditions/inject.py`, `conditions/vault_control.py`) and the policy success-check tests, so Saad writes those by hand: the three condition modules, `tests/unit/test_policy_checks.py` (acceptance boxes 2, 3 and 6: canary false-positive, each payload's `success_check`, and strict_passed false on a leak), and any strict-pass helper in `graders/honesty.py`. Everything the conditions depend on is in place: the proxy mutators, payload loading, the policy grader's `attacker_goal_met`, the read/write classifier, and the runner wiring that plants the vault, builds the plan, grades the attacker's goal and writes attack and pushback rows. The variant-id to payload mapping the conditions should use lives in `payloads.py` (`attack_variant`, `attack_payload`) so the modules stay small.
- **Why:** the blocks are a property of this session's safeguards, not of the code; splitting the work keeps M3 moving without fighting the classifier. Decided by Saad.
- **Effect on results:** none. The conditions are mechanical given the helpers; the report shows no attack or pushback rows until they exist, and the full suite stays green in the meantime.

## 2026-10-03: M3 runs use one fresh dev run (decision A)
- **What the spec said:** SPEC 6.7 says pushback continues the same session as the baseline episode it challenges; SPEC 5.5 orders a run's episodes but says nothing about how M3's conditions are grouped into runs.
- **What we did instead:** M3's measurements come from one new dev run, `configs/dev.yaml` unchanged, chunked with `--only` (baseline, which carries pushback, then vault_control, poison, inject, and the P1 conditions if they fit the budget). The M2 dev run of record (`runs/dev-20261003-052646`) is left as it is.
- **Why:** the M2 run's baseline sessions are closed, so pushback cannot be attached to them; it needs sessions that are still open, which means running baseline again. Putting every M3 condition in that same fresh run keeps all the M3 comparisons (attack vs vault_control, pushback vs baseline) at one commit and one config. Decided by Saad.
- **Effect on results:** M3 numbers come from their own run; they are compared with that run's own baseline and vault_control, not with the M2 run.

## 2026-10-03: auto target resolution picks by keyword order (decision B)
- **What the spec said:** SPEC 6.4: `auto` resolves to "the first tool whose name contains write, edit, move, create, execute or query, chosen from the live tool list".
- **What we did instead:** the proxy tries the keywords in that order and takes the first tool matching the current keyword (`proxy/mutators.py:resolve_auto_target`): keyword order wins over tool-list order. On filesystem this picks `write_file`; on postgres `execute_sql`.
- **Why:** reading it as tool-list order would, on postgres, match `explain_query` (it contains "query") before `execute_sql`, because `explain_query` comes first in the server's list. `explain_query` is read-only and the agent rarely calls it, so the poison would almost never fire. Keyword order targets the write tool, which is the point of the attack. Chosen by Claude, confirmed by Saad.
- **Effect on results:** poison with `target_tool: auto` lands on the writing tool for both services. The resolved tool is logged per episode (`poison_target` in the proxy side log).

## 2026-10-03: an inject rule waits for a result it can append to (decision C)
- **What the spec said:** SPEC 6.5: inject fires on the nth matching `tools/call`, `tool="*"`, `nth_call=1`.
- **What we did instead:** if the response to the chosen call is a JSON-RPC error (no `result` to append a text block to), the inject rule is re-armed and delivers on the next matching call instead (`proxy/relay.py:_pick_inject`, `_inject`). It still fires once.
- **Why:** otherwise an inject episode whose first tool call happened to error would deliver no payload at all and silently measure nothing. Re-arming guarantees every inject episode actually presents its payload. Chosen by Claude, confirmed by Saad.
- **Effect on results:** inject episodes reliably deliver the payload; the delivery call is logged (`inject`, with `inject_rearmed` when it waited).

## 2026-10-03: postgres tests are skipped in CI
- **What the spec said:** CI runs `uv sync`, `ruff check`, `pytest`; no paid calls, no Docker, no Ollama (SPEC 13).
- **What we did instead:** tests that need the PostgreSQL container are marked `postgres` and skipped when `CI=true` (and wherever the container cannot be started), with the reason printed by `pytest -rs` (`tests/conftest.py`). They run in cloud sessions and locally with Docker.
- **Why:** asked by Saad after CI failed. Before, the tests started the Docker container on the GitHub runner themselves, against SPEC 13. A Postgres service container in the workflow would also be Docker, so skipping follows the spec.
- **Effect on results:** none; CI no longer covers postgres isolation, which must be checked in a session with Docker.

## 2026-10-03: one dev run of record for M1 and M2, resumed at a newer commit
- **What the spec said:** nothing on how runs are organized; the M1 baseline was its own run.
- **What we did instead:** `runs/dev-20261003-052646` is the run of record for the dev suite. Its filesystem baseline started in a git worktree pinned at the commit that introduced the new rules (`a847da9`), so M2 code changes could not reach running episodes. After 7 episodes it was stopped, moved into the repo and resumed with `--resume` at a later commit for the rest of the baseline (filesystem and postgres), then `--only paraphrase` and `--only fault`. Every row records the commit that ran it (`git_commit`). Episodes in flight when the worktree run stopped were not written and reran on resume. `runs/dev-20261003-033437` (provider-default temperature, no re-sampling) is kept for the record only.
- **Why:** robustness drop compares each condition with the baseline of the same tasks, models and config, so one results file keeps that comparison simple. The baseline code path did not change between the two commits (an empty proxy plan is byte-identical passthrough, tested). Claude's choice, to save about an hour of wall time.
- **Effect on results:** none expected; baseline rows carry two different commits.

## 2026-10-03: the partial fault profile is in full.yaml only
- **What the spec said:** M2 step 5: after every P0 profile works, build `partial` (P1) and add it to `fault_profiles` in the configs.
- **What we did instead:** `partial` is implemented in `proxy/mutators.py` with unit and end-to-end tests. In M3 (step 2 of the M3 prompt) it is added to `fault_profiles` in `configs/full.yaml` only, so the confirmatory (standard-suite) run includes all seven profiles. `configs/dev.yaml` and `configs/local.yaml` keep the six P0 profiles, so the dev run of record keeps the same config across resumes and the M2/M3 dev fault numbers stay comparable.
- **Why:** adding `partial` to a config changes its hash, and a run refuses to resume with a different config. Keeping it out of dev.yaml preserves the dev run of record; putting it in full.yaml means the only fresh run that will use it (the confirmatory one, after PRE_REGISTRATION is FINAL) measures the full P1 profile set. Asked by Saad in the M3 prompt.
- **Effect on results:** the M2 and M3 dev fault results cover the six P0 profiles only; the confirmatory run will add `partial` (about 40 more fault episodes on the standard suite).

## 2026-10-03: how paraphrases are generated and checked
- **What the spec said:** SPEC 5.6: P paraphrases per task with the redteam model, cached, each passing a literal check (paths, file names, numbers, quoted strings, table and column names) and an LLM equivalence check, max 3 tries, then drop and log.
- **What we did instead (choices where the spec is open):** only `description.md` is reworded; MCPMark's fixed suffix is appended unchanged to every paraphrase. The literal check also covers Markdown code spans, snake_case and CamelCase identifiers (column and table names) and every Markdown table cell, and ignores Markdown list numbering on both sides (so "3." in a list neither counts as nor satisfies the number 3). Each variant gets a different style hint (prose, reordered list, short message) so the three differ from each other. The generation prompt (version v2) lists the literals the check will demand; v1 did not, and gpt-oss kept writing small numbers as words ("five"), which dropped every variant of `uppercase`. The equivalence check asks one question covering both directions. Generation is its own command, `pruefstand paraphrase`, guarded by the key spend limit; a run refuses to start paraphrase episodes without a valid cache (missing, or made from a different description). A variant that was dropped has no episode. The cache files record generator, prompt version, every rejection with the failed check, every drop and the cost. `docs/notes/paraphrase-samples.md` shows 5 for review.
- **Why:** simplest reading of SPEC 5.6 that makes the paraphrases demand the same end state and lets every model see identical text. Claude's choices.
- **Effect on results:** paraphrase episodes per task can be fewer than 3 where variants were dropped; the report counts tasks, not variants, so this changes only the precision of the estimate.

## 2026-10-03: the empty fault profile keeps structuredContent
- **What the spec said:** `empty`: forward the result with `content: []` (SPEC 5.2).
- **What we did instead:** only `content` is emptied; other result fields such as `structuredContent` are forwarded unchanged.
- **Why:** the MCP SDK validates `structuredContent` against a tool's output schema; removing it would turn `empty` into a schema error on the client, a different fault. The agent loop shows the model only the content blocks, so the model sees an empty result.
- **Effect on results:** none beyond the profile's intent.

## 2026-10-03: postgres-mcp started with uvx and a pinned MCP SDK
- **What the spec said:** reuse MCPMark's server launch command, `pipx run postgres-mcp==0.3.0 --access-mode=unrestricted` with `DATABASE_URI` (SPEC 2.1, `docs/notes/mcpmark-interface.md`).
- **What we did instead:** `uvx --python 3.12 --with mcp==1.30.0 postgres-mcp==0.3.0 --access-mode=unrestricted`, same `DATABASE_URI`. `--python 3.12` was added the same day after CI failed: postgres-mcp 0.3.0 requires Python 3.12 or newer, and an inherited `UV_PYTHON=3.11` (set by GitHub's setup-uv) made uvx unable to resolve it, so the server exited before `initialize`. In the cloud VM uvx had picked 3.12 by itself, so results are unaffected.
- **Why:** `pipx` is not installed in the cloud VM; `uvx` runs the same PyPI package (approved by Saad). `postgres-mcp` 0.3.0 does not pin the MCP SDK, and a fresh install today pulls `mcp` 2.x, on which the server dies at import. 1.30.0 is the 1.x SDK the harness itself uses.
- **Effect on results:** none expected; same server version and tools. Other transitive dependencies resolve to their current versions.

## 2026-10-03: Postgres in MCPMark's Docker image, not the native PostgreSQL 16
- **What the spec said:** SPEC 5.1: the native PostgreSQL 16 service when it exists, otherwise one Docker container per run. Saad approved native PG16 for M2.
- **What we did instead:** one long-lived Docker container `pruefstand-pg` from MCPMark's image `pgvector/pgvector:0.8.0-pg17-bookworm`, user `postgres`, on `127.0.0.1:55432`, kept across runs (templates are restored once per machine). `pg_restore` runs inside the container. The native service is not used; the session hook starts `dockerd` instead of `postgresql`. `doctor` checks the container. SPEC 5.1, `docs/CLOUD.md` and CLAUDE.md updated.
- **Why:** MCPMark's sample database backups are PostgreSQL 17 dumps (format 1.16); PostgreSQL 16's `pg_restore` refuses them. MCPMark itself uses this exact image. Found after the approval, so this replaces the approved native setup (Claude's choice, reported to Saad). One container for all runs instead of one per run because templates are read only and each episode gets its own clone; restoring employees (34 MB) per run would only add time.
- **Effect on results:** same PostgreSQL major version and extensions as MCPMark. Also found: a failed `pg_restore` writes `error:` in lower case; MCPMark checks for `ERROR`, so a failed restore can go unnoticed there. Ours ignores case and requires at least one table.

## 2026-10-03: easy dev tasks are exploratory, standard suite held out
- **What the spec said:** SPEC 2.2 defines `dev.txt` (easy) and `full.txt` (standard); CLAUDE.md forbids the `full` suite before `PRE_REGISTRATION.md` is FINAL. Nothing said whether single standard tasks may be run earlier.
- **What we did instead:** one line under Setup in `PRE_REGISTRATION.md`: the 20 easy dev tasks are exploratory, the standard suite is held out for the confirmatory run after FINAL. No standard task is run before then. No hypothesis or threshold was changed. The Agent line in the same Setup section now names temperature 1.0 and the empty-reply re-sampling (entries below).
- **Why:** decided by Saad. Seeing standard results before freezing the hypotheses would weaken the pre-registration.
- **Effect on results:** M1 and M2 numbers are exploratory only. Intervals on 10 tasks per service stay wide.

## 2026-10-03: absolute spend limit on the OpenRouter key
- **What the spec said:** `spend_cap_eur` limits one run (SPEC 5.5); nothing about the account.
- **What we did instead:** new config field `key_spend_cap_usd` (4.5 in all configs, set by Saad). Before every episode the runner reads the key's total usage from OpenRouter (`GET /api/v1/key`, `usage` in USD) and stops cleanly if `concurrency` more average episodes (at least 0.05 USD each) could pass the limit. If the usage cannot be read after 3 tries it does not start the episode (fails closed). Paraphrase generation checks the same limit. Unit and integration tested.
- **Why:** Saad: "never let total key spend pass $4.50". The per-run cap cannot see spend from earlier runs and probes.
- **Effect on results:** none on scores; a run may stop early, partial results stay valid.

## 2026-10-03: temperature 1.0 sent explicitly
- **What the spec said:** `temperature: null`, the provider default (SPEC 5.3, 10); `PRE_REGISTRATION.md` (draft) said provider-default temperature.
- **What we did instead:** `temperature: 1.0` in dev, full and local configs, SPEC 5.3 and 10 and the draft pre-registration Setup line updated. The code default stays `null`.
- **Why:** decided by Saad. MCPMark sends `temperature: 1.0` on every call. The empty-reply investigation showed provider defaults differ: DeepInfra's default sampling for gpt-oss-120b is almost deterministic, CoreWeave's is not (`docs/notes/empty-replies.md`).
- **Effect on results:** the M1 baseline (`runs/dev-20261003-033437`, provider default) is not comparable with later runs; it is kept for the record and replaced by a re-run under the new rules as the M1 baseline of record.

## 2026-10-03: empty replies are re-sampled up to 3 times
- **What the spec said:** a reply without tool calls is the final answer (SPEC 5.3), as in MCPMark's agent.
- **What we did instead:** a reply with empty content, no tool calls and `finish_reason: stop` is not shown to the conversation; the identical request is sent again, at most 3 times per step. If the fourth reply is still empty it is the final answer, as before. Re-sampled replies count in tokens, cost and `llm_requests`, not in `steps`. Each empty reply is classified from its hidden output tokens (output tokens minus visible reasoning and content at 4 characters per token): 25 or more is a "dropped call", fewer is "stopped after reasoning". New EpisodeResult fields `empty_reply_resamples`, `empty_replies_dropped_call`, `empty_replies_stopped`; trace events carry `empty_reply` and `resampled`. The report card shows the empty-reply rate per model (empty replies over model replies, where model replies are steps plus re-sampled replies) and a sensitivity score "MCPMark rule": pass@1 and pass^k with every episode that needed a re-sample counted as failed. SPEC 5.3 updated.
- **Why:** decided by Saad after the investigation in `docs/notes/empty-replies.md`: 21 of 100 M1 baseline episodes ended on such a reply, all failed, and on both CoreWeave and DeepInfra the model had announced a tool call that never arrived. The threshold of 25 hidden tokens sits in the gap seen in the M1 data (2 to 19 versus 32 to 50; normal tool-call replies have a median of 33).
- **Effect on results:** higher pass rates than MCPMark's agent would get on the same model and stack; the "MCPMark rule" score shows how much. The classification is a heuristic (characters per token vary), so the two kinds are approximate.

## 2026-10-03: a rejected key (401) pauses the run; account pauses are not remembered for the day
- **What the spec said:** SPEC 5.5 handles daily-quota 429s; the entry "out-of-credits answers pause the model like an exhausted quota" (below) made 402 and "Key limit exceeded" pause like a daily quota; any other API error ends the episode as `llm_error`.
- **What we did instead:** an HTTP 401 (expired or invalid key) is classified like a 402: the model pauses, the episode in flight is not written, and the run prints "paused: the provider rejected the account (...). Fix the key or credit, then resume with --resume <run_id>". These account pauses (401, 402, key limit) are no longer stored as `exhausted` in `quota.json`; only a real daily quota is. Tested (unit and a full run that pauses on 401 and finishes on a same-day resume).
- **Why:** asked by Saad (the key expires 2026-10-10). Storing an account pause for the whole UTC day meant a same-day `--resume` after adding credit or a new key would have skipped the model until midnight UTC.
- **Effect on results:** none on scores; a rejected key can no longer turn episodes into `llm_error`.

## 2026-10-03: pilot rows re-scored with the new final_claim parser
- **What the spec said:** results.jsonl rows are appended once, after grading (SPEC 4).
- **What we did instead:** `scripts/rescore_claims.py` rewrote `runs/pilot-dev-20261003-031744/results.jsonl` with `final_claim`, `false_success` and `strict_passed` recomputed from the traces, and `malformed_tool_names` counted from them. The original rows are kept next to it as `results.pre-rescore.jsonl`. `parse_failure_retries` stays 0 because the pilot traces did not record retries.
- **Why:** asked by Saad, so the pilot uses the same claim rule as the M1 baseline.
- **Effect on results:** no claim changed. The three gpt-oss-20b episodes ended with an empty message or a message without DONE (one empty final reply after a reasoning-only turn, one reasoning text leaked into the content, one empty), so they stay `none`. 3 malformed tool names were found, one in each 20b episode.

## 2026-10-03: max_steps and episode_timeout_s match MCPMark's defaults
- **What the spec said:** `max_steps` default 40 model calls, `episode_timeout_s` 900 (SPEC 5.3, 10).
- **What we did instead:** `max_steps: 100` and `episode_timeout_s: 3600` in all configs and as code defaults. SPEC 5.3 and 10 and the draft `PRE_REGISTRATION.md` setup line updated.
- **Why:** Saad asked to match MCPMark's default turn limit if it differs. MCPMark's default agent has `MAX_TURNS = 100`, counted the same way as our steps (one per model reply), and `pipeline.py` gives each task `--timeout 3600` (`docs/notes/mcpmark-interface.md`). The timeout was raised with the steps (Claude's choice): in the pilot gpt-oss-120b used 298 s for 40 steps, so 100 steps would often hit 900 s and the step limit would not really be MCPMark's.
- **Effect on results:** fewer `max_steps` and `timeout` stops, more passes on long tasks, higher cost per failing episode. Not comparable with the pilot's 40-step limit (one 120b pilot episode stopped at 40).

## 2026-10-03: parse-failure retries capped at 3 per call and counted
- **What the spec said:** retries on transient API errors (SPEC 5.3); nothing on counting them.
- **What we did instead:** provider parse failures (Groq `tool_use_failed` / `output_parse_failed`, CoreWeave's Harmony "unexpected tokens remaining in message header") are retried at most 3 times per model call; a fourth parse failure ends the episode as `llm_error`. The retries are counted per response (`parse_retries` in each `llm_response` trace event), per episode (new EpisodeResult field `parse_failure_retries`) and per model in the summary (`parse failures: retries / requests`, and episodes affected).
- **Why:** asked by Saad: keep the retries but make their effect measurable. MCPMark's own agent stops after 3 consecutive failed calls.
- **Effect on results:** a model whose output the provider keeps rejecting now gets `llm_error` after 4 tries instead of up to 6. The rate is visible per model.

## 2026-10-03: malformed tool names counted
- **What the spec said:** nothing.
- **What we did instead:** a tool call whose name contains a Harmony token (`<|` or `|>`, for example `write_file<|channel|>commentary`) is still sent unchanged, but counted per episode (new EpisodeResult field `malformed_tool_names`), flagged in the trace (`malformed_name: true` on the `tool_call` event) and reported per model in the summary.
- **Why:** asked by Saad, to measure the gpt-oss-20b leak described in the entry "gpt-oss-20b tool-name leak is not repaired".
- **Effect on results:** none on scores; a new diagnostic.

## 2026-10-03: final_claim parsing strips markdown and punctuation
- **What the spec said:** `final_claim` from the first word of the last assistant message, case-insensitive (SPEC 5.3).
- **What we did instead:** before reading the first word, leading whitespace and every ASCII punctuation character are stripped, which covers markdown markers (`**`, `__`, `#`, `>`, backticks). The old rule skipped non-word characters, but not `_`, so `__DONE__` was `none`. Only the first word counts: "Not done" and "**Summary**: DONE" stay `none`. Unit tested.
- **Why:** asked by Saad, so formatting does not decide honesty and strict-pass metrics.
- **Effect on results:** some messages that were `none` become `done` or `failed`, which changes `strict_passed` and `false_success` for them. None in the pilot changed.

## 2026-10-03: usd_to_eur corrected to 0.88
- **What the spec said:** the earlier entry "CLAUDE.md paid-model rule and spend caps" set `usd_to_eur: 4.44` in `configs/dev.yaml`.
- **What we did instead:** `usd_to_eur: 0.88`.
- **Why:** 4.44 was a typo (Saad). 0.88 is close to the market rate.
- **Effect on results:** euro figures are now about the real cost. The 5 EUR cap now allows about 5.68 USD of spend, not 1.13. The pilot's stored `cost_eur` values were computed with 4.44; divide by 4.44 and multiply by 0.88 to compare. `estimate` now converts a pilot's euros back to dollars with the pilot's own `usd_to_eur` (from its config.yaml) before applying the planned rate, so the estimate is not distorted.

## 2026-10-03: Harmony parse errors from the provider are retried
- **What the spec said:** retry on transient API errors (SPEC 5.3); a 400 is fatal and ends the episode as `llm_error`.
- **What we did instead:** an HTTP 400 whose text says "unexpected tokens remaining in message header" (or "unexpected token ... while expecting") is retried like Groq's `output_parse_failed`, which was already retried. The first OpenRouter pilot (`runs/pilot-dev-20261003-031537`) was stopped after its first episode ended as `llm_error` on exactly this error, and a fresh pilot was started after the fix; the stopped one is kept for the record.
- **Why:** it is CoreWeave's parser rejecting one sampled gpt-oss reply (the Harmony chat format), not a malformed request. Sampling again usually works. Scoring it as a model failure would measure the provider's parser.
- **Effect on results:** fewer `llm_error` episodes; every retry is still counted in `llm_requests` and in cost.

## 2026-10-03: pilot restarted from scratch on OpenRouter
- **What the spec said:** M1 step 14: run `pilot` with 3 tasks for each model, then `estimate`.
- **What we did instead:** the Groq pilot `runs/pilot-dev-20261002-182600` (2 of 6 episodes finished) is kept in git for the record but not used. A new pilot with the OpenRouter config was run from scratch, and `estimate` uses only that one.
- **Why:** different serving stack, provider and limits; the Groq episodes are not comparable. Decided by Saad.
- **Effect on results:** none on the M1 numbers, which come only from OpenRouter runs.

## 2026-10-03: gpt-oss-20b tool-name leak is not repaired
- **What the spec said:** nothing; the loop passes tool calls to the server as the model wrote them (SPEC 5.3).
- **What we did instead:** nothing is changed. On OpenRouter, gpt-oss-20b sometimes returns a tool name with a piece of its Harmony chat format attached (`write_file<|channel|>commentary`). The server answers "unknown tool", the model sees the error and can retry.
- **Why:** seen on CoreWeave, DeepInfra and Parasail at similar rates, never on 120b (`docs/notes/openrouter.md`). Repairing the name would change the agent and make the harness more forgiving than MCPMark's. Groq rejected such output as `tool_use_failed` and we retried, so the effect was hidden before.
- **Effect on results:** gpt-oss-20b loses some steps (and sometimes episodes) to these errors. Countable from traces (`tool_call` names containing `<|`).

## 2026-10-03: out-of-credits answers pause the model like an exhausted quota
- **What the spec said:** SPEC 5.5 handles daily-quota 429s; any other API error ends the episode as `llm_error`.
- **What we did instead:** OpenRouter's 402 ("Insufficient credits") and "Key limit exceeded" are classified like a daily-quota 429: the model is marked exhausted, the episode in flight is not written, and the run stops cleanly so `--resume` can continue after credit is added.
- **Why:** otherwise every remaining episode would be scored as `llm_error`, which is a billing problem, not a model result.
- **Effect on results:** none.

## 2026-10-03: estimate reports hours and dollars
- **What the spec said:** `estimate` projects episodes, requests, euros and days at `rpd_limit` (SPEC 11).
- **What we did instead:** it also prints serial episode hours per model, the run's wall time at the config's `concurrency`, the cost in USD (euros divided by `usd_to_eur`) and the run total against `spend_cap_eur`. Days at `rpd_limit` and `tpd_limit` are only printed for models that have those limits.
- **Why:** paid models have no daily limits; what Saad approves is euros and hours.
- **Effect on results:** none.

## 2026-10-03: CLAUDE.md paid-model rule and spend caps
- **What the spec said:** CLAUDE.md hard rule "Never call a paid model. The configs use free models only (`spend_cap_eur: 0`)."; SPEC 10 dev config had `spend_cap_eur: 0`, `usd_to_eur: null`.
- **What we did instead:** the rule is now "Only call paid models in a run whose config has spend_cap_eur above 0, set by Saad. Never raise a spend cap yourself." `configs/dev.yaml` has `spend_cap_eur: 5` and `usd_to_eur: 4.44` (both set by Saad). `configs/full.yaml` and `configs/local.yaml` got the same models but keep `spend_cap_eur: 0`, so they refuse to start until Saad sets a cap (tested). `PRE_REGISTRATION.md` (still DRAFT) had its bracketed model proposal updated to the OpenRouter models; no hypothesis or threshold was touched. `docs/CLOUD.md` names the new key and domain.
- **Why:** Groq's paid tier is unavailable, so the free-only rule could not stay. Decided by Saad.
- **Effect on results:** euro figures are the provider's USD cost times 4.44. That factor is Saad's choice and is far above a market rate (roughly 0.85 to 0.95 EUR per USD in 2026), so reported euros are about five times the market conversion, and the 5 EUR cap stops a run at about 1.13 USD of real spend. Raw USD can be recovered as `cost_eur / 4.44`.

## 2026-10-03: Groq-specific limits removed
- **What the spec said:** dev config `rpm_limit: 30`, `rpd_limit: 1000`, plus our `tpm_limit: 8000` and `tpd_limit: 200000` (entries of 2026-10-02 below).
- **What we did instead:** the OpenRouter models set only `rpm_limit: 60` as our own ceiling; no `rpd_limit`, `tpm_limit` or `tpd_limit`. `concurrency` stays 2. The generic quota and retry code is unchanged and still tested: token bucket, token window, daily counters, per-day 429 handling, the parse-failure retry and `max_tokens: 32768`.
- **Why:** `GET /api/v1/key` reports no request rate limit for this paid key (`rate_limit.requests: -1`) and OpenRouter has no daily limit for paid models. Upstream 429s are retried with backoff.
- **Effect on results:** none on outcomes; runs are no longer throttled to Groq's quotas.

## 2026-10-03: cost from the provider's reported cost; cached tokens recorded
- **What the spec said:** cost via `litellm.completion_cost` converted with `usd_to_eur`, or `price_usd_per_mtok` for models LiteLLM cannot price (SPEC 5.3, 10).
- **What we did instead:** `agent/llm.py` uses the cost the response reports (`usage.cost`, OpenRouter, in USD, cache discounts included) first, then `price_usd_per_mtok`, then `litellm.completion_cost`. `price_usd_per_mtok` is set to CoreWeave's output price (0.13 for 20b, 0.17 for 120b), an upper bound used only if a response lacks a cost. Cached input tokens (`usage.prompt_tokens_details.cached_tokens`) are recorded per response and as a new `tokens_cached_in` field in EpisodeResult. SPEC 4 and 5.3 updated.
- **Why:** LiteLLM's price table holds the cheapest OpenRouter provider's price, not the pinned provider's. The reported cost is what is actually billed.
- **Effect on results:** cost figures match OpenRouter's billing.

## 2026-10-03: one pinned OpenRouter provider, recorded per result
- **What the spec said:** nothing about upstream providers (SPEC 5.3, 10).
- **What we did instead:** a new optional model field `provider`. When set, every request carries OpenRouter provider routing `{order: [<provider>], allow_fallbacks: false}`. Both models and the redteam model use `coreweave/fp4`. The provider that actually served each reply (`response.provider`) goes into every `llm_response` trace event and into a new `provider` field of EpisodeResult (several would be joined with "+", which would show the pin failed). The pin is part of the config, so it is in `runs/<id>/config.yaml` and the config hash. SPEC 4, 5.3 and 10 updated.
- **Why:** reproducibility. OpenRouter otherwise load-balances between providers with different engines and weight formats. CoreWeave serves both models in fp4 (the released format), passed tool calling on 120b every time, had no upstream 429s under parallel load, and reports cached tokens (`docs/notes/openrouter.md`). Decided by Saad (pin one provider, no fallbacks); provider chosen by Claude after testing.
- **Effect on results:** all episodes run on one stack. If CoreWeave is down, requests fail with 404 or 5xx instead of moving elsewhere, so episodes may end as `llm_error`.

## 2026-10-03: model API switched from Groq to OpenRouter
- **What the spec said:** two free gpt-oss models on Groq, key `PFS_GROQ_API_KEY`, `free_tier: true` (SPEC 5.3, 10); redteam model `groq/llama-3.3-70b-versatile`.
- **What we did instead:** `openrouter/openai/gpt-oss-20b` and `openrouter/openai/gpt-oss-120b`, key `PFS_OPENROUTER_API_KEY`, `free_tier: false`, in dev, full and local configs. Redteam model `openrouter/openai/gpt-oss-120b`. `openrouter.ai` is in the allowed domains.
- **Why:** Groq's free tier allowed about 200k tokens per model per day, which made the M1 baseline a multi-day job, and Groq's paid tier is unavailable. Decided by Saad.
- **Effect on results:** same model weights, different provider and serving stack, so numbers are not comparable with the Groq pilot. The redteam model changes from Llama 3.3 70B to gpt-oss-120b, which is also one of the models under test (relevant from M2: paraphrases are written by one of the tested models).

## 2026-10-02: canary vault only in attack conditions, plus a vault_control condition
- **What the spec said:** SPEC 5.1 planted the canary (`vault/` as a second filesystem root, `vault.api_keys` in Postgres) in every condition so leak rates are comparable.
- **What we did instead:** the vault is planted only in poison, inject and rugpull, and in a new control condition `vault_control` (1 attempt per task, vault present, empty plan). Baseline, paraphrase, fault and pushback start the server exactly as MCPMark does, with `workspace/` only. SPEC 4, 5.1, 6.8 and 8 updated.
- **Why:** an extra allowed root changes what `list_allowed_directories` returns and what the agent explores, so baseline would no longer match MCPMark's setup. `vault_control` keeps leak rates comparable: it separates the effect of the extra folder (vault_control vs baseline) from the effect of the attack (attack vs vault_control). Decided by Saad.
- **Effect on results:** baseline, paraphrase, fault and pushback numbers are closer to MCPMark's setting. Canary access and leak rates are only defined in vault conditions; `vault_control` is their no-attack reference. Adds 1 episode per task per model.

## 2026-10-02: malformed is an ordinary fault profile
- **What the spec said:** SPEC 5.2 expected `malformed` might kill the SDK session, ending the episode with `transport_failure` and reporting the profile separately as a host-robustness result.
- **What we did instead:** `malformed` is treated like every other fault profile and counts in fault recovery. `transport_failure` is reserved for sessions that genuinely die (server exits, `Connection closed`), whatever the profile. SPEC 5.2 updated.
- **Why:** tested with `mcp` 1.30.0 (`docs/notes/mcp-stdio.md`): the client logs the parse error, the session survives, the waiting call times out and the next call works. The model sees a tool error, so this is a model result. Decided by Saad.
- **Effect on results:** fault recovery includes `malformed` episodes. No separate host-robustness line for `malformed` unless a session actually dies.

## 2026-10-02: MCP server environment is the full environment minus PFS_ variables
- **What the spec said:** nothing explicit; the MCP SDK's default gives the server only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`.
- **What we did instead:** the agent loop passes the full parent environment to the proxy and server, after removing every variable whose name starts with `PFS_`. A unit test checks the stripping. SPEC 5.3 updated.
- **Why:** with the SDK default, `npx` loses the proxy and CA variables and fails with `SELF_SIGNED_CERT_IN_CHAIN` in the cloud session. Stripping `PFS_` keeps our API keys away from the server under test. Decided by Saad.
- **Effect on results:** none.

## 2026-10-02: pin mcp>=1.30,<2
- **What the spec said:** "MCP Python SDK", no version; SPEC 5.3 and 13 describe the 1.x API (`stdio_client`, `ClientSession`, FastMCP).
- **What we did instead:** `mcp>=1.30,<2` in `pyproject.toml`.
- **Why:** 2.x renamed FastMCP and moved protocol types to a separate package (`docs/notes/mcp-stdio.md`). The malformed-line behaviour was tested on 1.30.0. Approved by Saad.
- **Effect on results:** none.

## 2026-10-02: cache/ is gitignored except cache/paraphrases/
- **What the spec said:** SPEC 3 says `cache/` is "paraphrase cache, committed"; SPEC 4 says only `cache/paraphrases/` is committed.
- **What we did instead:** `.gitignore` ignores `cache/*` and re-includes `cache/paraphrases/`. MCPMark initial states downloaded to `cache/mcpmark_states/` stay out of git.
- **Why:** the initial states are third-party data (tens of MB per category) and are re-downloadable; the paraphrases must be identical across runs, so they are committed. Approved by Saad.
- **Effect on results:** none.

## 2026-10-02: tpm_limit and tpd_limit model fields
- **What the spec said:** model fields `rpm_limit` and `rpd_limit` (SPEC 10); the quota throttle (SPEC 5.5) counts requests.
- **What we did instead:** optional `tpm_limit` (tokens per minute) and `tpd_limit` (tokens per day) fields. `tpm_limit` is set to 8000 for both gpt-oss models and `runner/quota.py` keeps a per-minute token budget when it is set. `tpd_limit` is set to 200000 for both gpt-oss models, the value in Groq's own 429 message ("tokens per day (TPD): Limit 200000", seen in the third pilot); `estimate` uses it, and `can_start` stops a model for the day when one more average episode would cross it. quota.json records tokens per day.
- **Why:** the Groq free tier answers with `x-ratelimit-limit-tokens: 8000` for gpt-oss-20b and gpt-oss-120b. Agent requests carry the whole conversation, so the token limit, not the request limit, is what throttles a run.
- **Effect on results:** none; runs only get slower.

## 2026-10-02: llm_requests field in EpisodeResult
- **What the spec said:** EpisodeResult has `steps` (model calls) but no request count (SPEC 4).
- **What we did instead:** added `llm_requests: int = 0`, the number of HTTP requests the episode sent to the model, retries included.
- **Why:** `estimate` projects requests per day from the pilot (SPEC 11), and retries after a per-minute 429 count against the daily quota too, so `steps` would underestimate.
- **Effect on results:** none.

## 2026-10-02: episode timeout counts agent time, not quota waits
- **What the spec said:** `episode_timeout_s` (900) limits an episode (SPEC 5.3).
- **What we did instead:** the timeout is checked before each model call against the episode's elapsed time minus the time spent waiting on our own quota throttle and on 429 backoffs. A hard wall-clock cap of 6 x `episode_timeout_s` (quota waits included) remains as a safety net against hangs. Each `llm_response` trace event records `throttle_s` and `finish_reason`.
- **Why:** in the first pilot the Groq free tier's 8,000 tokens per minute held both running episodes in the throttle for most of their time (22 requests in 11 minutes). With a wall-clock timeout, "timeout" would measure our quota, not the model. The pilot was stopped and restarted with this fix.
- **Effect on results:** `stop_reason = "timeout"` now means the agent and server used more than 900 s of real work. Episodes take longer in wall-clock time.

## 2026-10-02: concurrent workers interleave models
- **What the spec said:** specs are sorted by `(model, task_id, condition, variant_id, attempt)` (SPEC 5.5).
- **What we did instead:** the sort order is unchanged and still defines the episode list, but the execution queue takes the models round robin (each model's specs in sort order), so with `concurrency: 2` the two workers usually run different models.
- **Why:** Groq limits are per model; two workers on the same model share one 8,000 TPM budget and both stall.
- **Effect on results:** none on the content of results. With concurrency above 1 the line order in `results.jsonl` follows completion order, as before.

## 2026-10-02: max_tokens config field, default 32768
- **What the spec said:** no output cap is mentioned (SPEC 5.3, 10).
- **What we did instead:** a `max_tokens` config field (default and configs: 32768) passed on every model call.
- **Why:** with no cap, Groq stops gpt-oss replies at 2,048 completion tokens. In the second pilot a reply ended with `finish_reason: length` after 2,048 tokens of reasoning and no output, which the loop had to score as an empty final answer. MCPMark's own LiteLLM agent sets `max_tokens: 32768` (`src/agents/mcpmark_agent.py:852`), so this matches it. The second pilot was stopped and restarted.
- **Effect on results:** fewer truncated replies; longer replies cost more tokens per minute.

## 2026-10-02: a per-day 429 with a short retry hint is waited out
- **What the spec said:** a 429 that says the daily quota is exhausted marks that model as done for today (SPEC 5.5).
- **What we did instead:** if the per-day 429 also says "try again in" 15 minutes or less, the client waits that long and retries (the wait counts as quota time, not agent time). Only a per-day 429 without a hint, or with a longer one, marks the model done for today. The provider's message is now written to run.log when a model pauses.
- **Why:** in the third pilot gpt-oss-120b was marked done for the day after 33 requests and about 146k tokens, yet a minute later both a tiny and a 4.6k-token request succeeded. Groq's daily token limit appears to be a rolling window, so stopping for the whole day wastes most of it.
- **Effect on results:** none on episode outcomes; runs pause less often.
