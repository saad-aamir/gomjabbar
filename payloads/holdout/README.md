# Held-out payloads

Payloads in this folder are written by Saad **after** the defense patterns were frozen at the git tag `defense-patterns-v1` (DEVIATIONS.md, 2026-10-04). They exist to measure the defenses on attacks nobody tuned the patterns against.

Rules:

- **Never use these payloads to tune the defense patterns.** The patterns in `src/pruefstand/defenses/patterns.py` are frozen; a unit test fails if that file changes.
- Same YAML format as `payloads/poisoning/` and `payloads/injection/` (SPEC 7). A file with a `mode` field is a poisoning payload; a file without one is an injection payload.
- Ids must be unique across this folder. Variant ids become `poison-holdout-<id>` and `inject-holdout-<id>`.
- They are used only by runs whose config says `payload_set: holdout` (`configs/holdout.yaml`, `configs/holdout-defended.yaml`).
