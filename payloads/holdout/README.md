# Held-out payloads

Payloads in this folder were written **after** the defense patterns were frozen at the git tag `defense-patterns-v1` (DEVIATIONS.md, 2026-10-04). They exist to measure the defenses on attacks nobody tuned the patterns against.

The held-out payloads were written in a separate chat with Claude (not Claude Code) after the freeze, by an author who had seen only a short summary of the pattern categories, never the pattern file. Round 1 wording was too weak; round 2 increased the pressure while keeping new wording.

The two files here are round 2. The round 1 texts (`debug-trace`, `confirm-value`) are in commit `de4728e` and were run as `runs/holdout-20261004-214818` and `runs/holdout-defended-20261004-222423`.

Rules:

- **Never use these payloads to tune the defense patterns.** The patterns in `src/gomjabbar/defenses/patterns.py` are frozen; a unit test fails if that file changes.
- Same YAML format as `payloads/poisoning/` and `payloads/injection/` (SPEC 7). A file with a `mode` field is a poisoning payload; a file without one is an injection payload.
- Ids must be unique across this folder. Variant ids become `poison-holdout-<id>` and `inject-holdout-<id>`.
- They are used only by runs whose config says `payload_set: holdout` (`configs/holdout.yaml`, `configs/holdout-defended.yaml`).
