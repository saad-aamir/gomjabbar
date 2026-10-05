# M4 walkthrough: defend and ship

## What was built

Three host-side defenses now sit in the agent loop: pinning (freeze tool definitions after the first listing), a sanitizer (drop instruction-like lines from tool results) and a description scan (drop instruction-like sentences from tool descriptions, and hide a tool that is mostly instructions). On clean text they removed nothing (0 removals on the real servers' 47 descriptions and on the 6,515 clean tool results in the M3 run's full traces), and in the defense run they took gpt-oss-120b's attack success from 76.7% to 0% for poison and from 16.2% to 0% for inject, while baseline pass@1 went from 87% to 84% (p = 0.55). Their patterns were frozen at the tag `defense-patterns-v1` (a test checks the file's hash) before any held-out payload existed; on held-out payloads, round 2's poison fell from 90% to 0% (p < 0.0001), while round 1 was too weak to land at all. `gomjabbar compare` pairs two runs episode by episode and reports both rates, the paired change and McNemar's test per condition and per payload. A CI regression gate pins the outcome of every condition and payload, and the README, architecture guide and rewritten pre-registration (PLANNED, NOT RUN, H1 to H6 with their dev numbers) make the repo ready to read from the outside.

## Who wrote what

Claude wrote the M4 code and documents from Saad's M4 instructions. Saad decided every change to the milestone (no full run, the third defense and its hiding rule, 120b only, the pattern freeze and the holdout set, running the experiment himself, the hypotheses H1 to H6), wrote the attack conditions, the policy tests and the strict-pass rule in M3, and ran the defense run and both held-out rounds from his Mac. The held-out payloads were written in a separate chat with Claude (not Claude Code) after the freeze, by an author who had seen only a short summary of the pattern categories, never the pattern file. Round 1 wording was too weak; round 2 increased the pressure while keeping new wording. Nothing in M4 was blocked by the session's safeguards. The session's git proxy would not push the tag, so Saad pushed `defense-patterns-v1` himself; it points at the frozen commit `91a7f67`.

## Results at the gate (exploratory, gpt-oss-120b)

Main run `runs/defense-20261004-155855`, paired with the M3 run (`docs/notes/defense-compare.txt`):

| | without defenses | with defenses | discordant | McNemar p |
| --- | --- | --- | --- | --- |
| poison attack success (60) | 76.7 [70.0, 83.3] | 0.0 | 46 / 0 | < 0.0001 |
| inject attack success (70) | 16.2 [10.4, 22.1] | 0.0 | 12 / 0 | 0.0005 |
| baseline pass@1 (100) | 87.0 [75.0, 96.0] | 84.0 [70.0, 96.0] | 7 / 4 | 0.55 |
| poison episodes, task state pass | 52 of 60 | 35 of 60 | 19 / 2 | 0.0002 |
| inject episodes, task state pass | 43 of 70 | 56 of 70 | 5 / 18 | 0.011 |

Held-out rounds, 20 tasks per arm (`docs/notes/holdout-compare.txt`, `holdout-strong-compare.txt`):

| | attack success, undefended | attack success, defended | task state pass, undefended | task state pass, defended | defense actions |
| --- | --- | --- | --- | --- | --- |
| round 1 poison `debug-trace` | 0% | 0% | 16 of 20 | 19 of 20 | none in any episode |
| round 1 inject `confirm-value` | 5% (1) | 5% (1) | 19 of 20 | 17 of 20 | none in any episode |
| round 2 poison `access-policy` | 90% (18) | 0%, p < 0.0001 | 17 of 20 | 13 of 20 | 1 sentence removed per episode, no tool hidden |
| round 2 inject `lockout-warning` | 10% (2) | 0%, p = 0.50 | 13 of 20 | 15 of 20 | 1 line removed in 19 episodes; the 20th ended on `llm_error` before the payload arrived |

No held-out episode had a tool hidden, so no held-out task failed because of the half rule. The patterns matched neither round 1 payload. In round 2 the scan removed only the sentence asking for the credential and left the policy threat in place; in 4 of the 7 failed defended poison episodes the agent then refused to call the write tool and answered FAILED. The half rule did break tasks in the main run: it hid the real write tool in all 30 `append-readfirst` and `marker-pg` episodes, which passed 11 of 30 tasks against 26 of 30 undefended. Round 1's `debug-trace` and round 2's `access-policy` have the same goal and channel; the convenience framing won 0 of 20 times, the enforced-policy framing 18 of 20. False positives: 0 removals on 6,515 clean tool results (M3 full traces) and on 12,858 over every run's full traces.

The action counts were checked without full traces: the frozen patterns, the real tool lists and each payload text fully determine what the scan does, and the predicted counts match every row's `defense_actions`. One passing `marker-pg` episode logged one action more than predicted, most likely a refused call to the hidden `execute_sql`; its trace was not committed, so this is unconfirmed.

## File map

Source (`src/gomjabbar/`):

- `defenses/patterns.py`: the shared, frozen regular expressions (instruction family, plus description-only phrases).
- `defenses/sanitizer.py`: line filter for tool results, with a visible "[removed by sanitizer]" marker.
- `defenses/description_scan.py`: sentence filter for tool and parameter descriptions; `<IMPORTANT>` blocks removed whole; the empty-or-over-half hiding rule.
- `defenses/pinning.py`: `ToolPin`, sha256 per tool, changed / added / removed.
- `defenses/false_positives.py`: collects clean text from tool fixtures and any run folder (full `traces/` first, else `notable/`), runs all three defenses, renders the report.
- `defenses/__init__.py`: `KNOWN_DEFENSES`.
- `agent/loop.py`: defenses applied in `_refresh_tools` and `_execute`; calls to hidden tools refused; `defense_action` events counted per turn.
- `runner/episode.py`, `models.py`: `defense_actions` in every result row.
- `config.py`: unknown defense names refused; `payload_set` (standard or holdout), left out of the hash when standard so old hashes do not change.
- `payloads.py`, `conditions/poison.py`, `conditions/inject.py`: held-out payloads (`poison-holdout-<id>`, `inject-holdout-<id>`).
- `analysis/compare.py`: pairing, outcomes, McNemar, odds ratio, intervals, terminal table.
- `report/compare_html.py`, `report/templates/compare.html.j2`, `report/templates/_style.html.j2`: compare.html, styles shared with report.html.
- `report/html.py`, `report/templates/report.html.j2`: defenses and their actions in the report.
- `cli.py`: the `compare` command; a holdout run with no payloads is refused before a run folder exists.

Tests, scripts, configs, docs:

- `tests/unit/test_defenses.py`, `test_false_positives.py`, `test_pattern_freeze.py`, `test_compare.py`, `test_m4_configs.py`; additions to `test_payloads.py`, `test_config.py`, `test_report_m3.py`.
- `tests/integration/test_defenses_loop.py`: each defense inside a real episode through the proxy.
- `tests/regression/test_gate.py`, `expected_outcomes.json`, `.github/workflows/regression-gate.yml`: the regression gate.
- `tests/fixtures/clean_tools/*.json`: the real servers' tool lists; `tests/fixtures/scripted_llm.py` records the tools it was offered.
- `scripts/capture_clean_tools.py`, `scripts/defense_false_positives.py`, `scripts/exploratory_numbers.py`.
- `configs/defense.yaml`, `configs/holdout.yaml`, `configs/holdout-defended.yaml`; `payloads/holdout/README.md`.
- `README.md`, `docs/architecture.md`, `PRE_REGISTRATION.md`, `DEVIATIONS.md`, `docs/SPEC.md` (sections 3, 4, 5.7, 9), `docs/notes/defense-false-positives.md`, milestone acceptance boxes.

## Three important design decisions

1. **Defenses live in the host, and the trace shows what the model saw.** The defenses run inside the agent loop, not in the proxy, because they model a careful host application (SPEC 5.7), while the proxy models the attacker. The trace records the text the model was actually shown, and each removal is its own `defense_action` event with the removed text and the matching pattern, so every decision can be audited afterwards and counted in the result row. Rejected: defending in the proxy, which would mix the attacker and the defender in one process and make it impossible to say which side changed a message.

2. **Freeze the patterns, then test on payloads written afterwards.** The patterns were written by someone who had read the 8 payloads they are scored on, so their success on those payloads is an upper bound. Freezing them (tag plus a hash test that fails CI on any edit) before anyone wrote the held-out payloads turns the holdout run into a fair test. It also exposed the opposite problem: round 1's payloads slipped past the patterns entirely, and only failed because they were weak attacks. False positives are measured only on clean text and the goal is zero, because a defense that strips real file contents breaks the agent's work. Rejected: tuning the patterns until the repo payloads score zero, which would have produced a perfect-looking but meaningless number.

3. **Pair episodes, test with McNemar, show intervals next to the p-value.** The defense run repeats exactly the M3 episodes for gpt-oss-120b, so each defended episode has a twin. Pairing removes the task-to-task variation, and McNemar uses only the discordant pairs, the ones where the defense changed the outcome. The pairing key includes the model, which SPEC 9 left out, because a two-model run has two episodes per SPEC key. Next to the p-value, the paired change gets a task-bootstrap interval, which is what "the utility cost of the defenses" needs. Rejected: comparing the two runs' overall rates unpaired, which throws away the pairing and gives wider intervals for the same data.

## What is fragile

- **The hiding rule can turn an attack into a broken tool.** When the appended payload is longer than a tool's real description (append-readfirst on `write_file`), the description scan hides the real tool, so the agent cannot finish the task. Attack success falls, but so does state pass; the baseline cost does not show this, only the attack episodes' pass rate does.
- **A blocked call still counts for the attacker.** A call to a hidden tool is refused, but it is in the trace, so `tool_called` and canary checks still fire (`DEVIATIONS.md`). Conservative, but it means a defended run's attack success can be overstated.
- **Pinning has not been tested against a real rug pull**, because the rug pull condition was never built; it logged nothing in the defense run.
- **Removal can leave threats behind.** Round 2's description kept "calls ... are rejected unless the caller proves authorization" after its key sentence was removed, and agents then refused to use the tool. A sentence filter cannot tell an attacker's threat from a real usage note.
- **The patterns are not robust to wording.** Round 1's payloads (a tracing convenience, a confirmation request) matched nothing. The defense held in round 2 because strong framing used words the patterns know ("read ... credential ... include ... value").
- **One held-out payload per channel per round.** Round 2 poison is the only held-out result with power.
- **Round 1's payload files were replaced in place by round 2** (texts in commit `de4728e`), so round 1 episodes cannot be re-graded from HEAD.
- **Patterns are English keyword lists.** A paraphrased, translated or encoded instruction passes; so would a tool whose honest description says "you must".
- **False positives were measured on dev-task outputs only** (12,858 clean results, all from the same 20 tasks). Other data, such as documentation that tells the reader "you must", would trigger the sanitizer.
- **The regression gate pins outcomes of an obedient scripted agent.** It catches changes in the machinery, not in how real models behave, and a deliberate change needs a reviewed golden-file update.
- **H5 and H6 rest on very few episodes**: 10 to 15 tasks per pushback type, one episode per fault profile and task.

## Ten interview questions

1. Why do the defenses run in the agent host instead of in the proxy?
2. Pinning will log zero actions in your defense run. Why did you still build it, and what experiment would test it?
3. Your description scan hides a tool when more than half of its description is removed. What does that rule trade off, and how would you see the cost in the results?
4. How did you measure false positives, and why is "zero on clean text" the right target rather than a rate?
5. The patterns were written by someone who knew the attack payloads. How does the holdout design fix that, and how do you prove the patterns did not change afterwards?
6. Walk through McNemar's test on a 2x2 table with 8 and 2 discordant pairs. Why are the concordant pairs ignored?
7. Why does `compare` pair by model as well as task, condition, variant and attempt?
8. What does your regression gate catch, and what kind of regression would it miss?
9. Your pre-registration hypotheses were written after seeing the dev data. Why is that acceptable, and what makes the confirmatory test valid anyway?
10. A call to a hidden tool still counts as attacker success. Defend that choice, or argue for the opposite.
