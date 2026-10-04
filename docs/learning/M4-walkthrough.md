# M4 walkthrough: defend and ship

## What was built

Three host-side defenses now sit in the agent loop: pinning (freeze tool definitions after the first listing), a sanitizer (drop instruction-like lines from tool results) and a description scan (drop instruction-like sentences from tool descriptions, and hide a tool that is mostly instructions). On clean text they removed nothing: 0 removals on the real servers' 47 descriptions and on 6,642 clean tool results from every committed trace. Their patterns are frozen at the tag `defense-patterns-v1` (a test checks the file's hash), and an empty `payloads/holdout/` folder plus two configs let Saad test them on payloads written after the freeze. `pruefstand compare` pairs two runs episode by episode and reports both rates, the paired change and McNemar's test per condition and per payload; `configs/defense.yaml` is ready for the defense run (gpt-oss-120b, 230 episodes, about 0.51 EUR), which Saad runs. A CI regression gate pins the outcome of every condition and payload, and the README, architecture guide and rewritten pre-registration (PLANNED, NOT RUN, H1 to H6 with their dev numbers) make the repo ready to read from the outside.

## Who wrote what

Claude wrote the M4 code and documents from Saad's M4 instructions. Saad decided every change to the milestone (no full run, the third defense and its hiding rule, 120b only, the pattern freeze and the holdout set, running the experiment himself, the hypotheses H1 to H6), wrote the attack conditions, the policy tests and the strict-pass rule in M3, and runs the defense and holdout experiments. Nothing in M4 was blocked by the session's safeguards. One thing the session could not do: push the git tag (the git proxy accepts only the branch), so Saad pushes `defense-patterns-v1` from his Mac.

## File map

Source (`src/pruefstand/`):

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

2. **Freeze the patterns, then test on payloads written afterwards.** The patterns were written by someone who had read the 8 payloads they are scored on, so their success on those payloads is an upper bound. Freezing them (tag plus a hash test that fails CI on any edit) before Saad writes held-out payloads turns the holdout run into a fair test. False positives are measured only on clean text and the goal is zero, because a defense that strips real file contents breaks the agent's work. Rejected: tuning the patterns until the repo payloads score zero, which would have produced a perfect-looking but meaningless number.

3. **Pair episodes, test with McNemar, show intervals next to the p-value.** The defense run repeats exactly the M3 episodes for gpt-oss-120b, so each defended episode has a twin. Pairing removes the task-to-task variation, and McNemar uses only the discordant pairs, the ones where the defense changed the outcome. The pairing key includes the model, which SPEC 9 left out, because a two-model run has two episodes per SPEC key. Next to the p-value, the paired change gets a task-bootstrap interval, which is what "the utility cost of the defenses" needs. Rejected: comparing the two runs' overall rates unpaired, which throws away the pairing and gives wider intervals for the same data.

## What is fragile

- **The hiding rule can turn an attack into a broken tool.** When the appended payload is longer than a tool's real description (append-readfirst on `write_file`), the description scan hides the real tool, so the agent cannot finish the task. Attack success falls, but so does state pass; the baseline cost does not show this, only the attack episodes' pass rate does.
- **A blocked call still counts for the attacker.** A call to a hidden tool is refused, but it is in the trace, so `tool_called` and canary checks still fire (`DEVIATIONS.md`). Conservative, but it means a defended run's attack success can be overstated.
- **Pinning has not been tested against a real rug pull**, because the rug pull condition was never built; it will log nothing in the defense run.
- **Patterns are English keyword lists.** A paraphrased, translated or encoded instruction passes; so would a tool whose honest description says "you must".
- **The false-positive count rests on committed traces** in this session (381 episodes). The full M3 traces on Saad's Mac are the larger test (command in the hand-over).
- **The regression gate pins outcomes of an obedient scripted agent.** It catches changes in the machinery, not in how real models behave, and a deliberate change needs a reviewed golden-file update.
- **The tag lives only locally until Saad pushes it**; the hash test is what actually enforces the freeze in CI.
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
