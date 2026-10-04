# Defense false positives on clean text

Written by `scripts/defense_false_positives.py`. Each defense is run over clean text:
the real servers' tool descriptions (`tests/fixtures/clean_tools/`) and every tool
result in the run folders below that carries no payload sentence. A removal on clean
text is a false positive. The goal is zero.

- Runs: dev-20261003-033437, dev-20261003-052646, dev-20261003-205231, pilot-dev-20261002-182600, pilot-dev-20261003-031537, pilot-dev-20261003-031744, pilot-pgpilot-20261003-054747
- Episodes read: 381 (trace source: notable 381)
- Tool and parameter descriptions: 47
- Clean tool results: 6642 (1628 distinct texts checked)
- Results skipped as attack text (contain a payload sentence): 58

| Defense | Removals on tool descriptions | Removals on tool results |
| --- | --- | --- |
| pinning | 0 | n/a |
| sanitizer | 0 | 0 |
| description_scan | 0 | 0 |

## Every removal

None.
