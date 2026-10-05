# Defense false positives on clean text

Written by `scripts/defense_false_positives.py`. Each defense is run over clean text:
the real servers' tool descriptions (`tests/fixtures/clean_tools/`) and every tool
result in the run folders below that carries no payload sentence. A removal on clean
text is a false positive. The goal is zero.

- Runs: dev-20261003-205231
- Episodes read: 654 (trace source: traces 654)
- Tool and parameter descriptions: 47
- Clean tool results: 6515 (1767 distinct texts checked)
- Results skipped as attack text (contain a payload sentence): 138

| Defense | Removals on tool descriptions | Removals on tool results |
| --- | --- | --- |
| pinning | 0 | n/a |
| sanitizer | 0 | 0 |
| description_scan | 0 | 0 |

## Every removal

None.
