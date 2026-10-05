"""Sanitizer defense: remove instruction-like lines from tool results before the model sees them.

What: `sanitize(text)` splits a tool result into lines, drops every line that matches the
shared instruction patterns, and returns the cleaned text plus one `Removal` per dropped line.
Why: indirect prompt injection arrives inside tool output (a file, a query result, an error
message). A careful host can strip text shaped like an order before it reaches the model
(SPEC 5.7). It is a heuristic: a line-level filter, not an understanding of the text.
How: agent/loop.py calls `sanitize` on every tool result when "sanitizer" is in the episode's
defenses, sends the cleaned text to the model, and writes one `defense_action` trace event
per removal. scripts/defense_false_positives.py runs it over clean tool results.
"""

from __future__ import annotations

from dataclasses import dataclass

from gomjabbar.defenses.patterns import INSTRUCTION_PATTERNS, find_match

# Shown to the model in place of the removed lines, so it knows something was taken out.
REMOVED_MARKER = "[removed by sanitizer]"


@dataclass
class Removal:
    """One line the sanitizer took out, with the pattern that matched it."""

    text: str
    pattern: str


def sanitize(text: str) -> tuple[str, list[Removal]]:
    """Drop instruction-like lines from a tool result; return the new text and the removals."""
    kept: list[str] = []
    removals: list[Removal] = []
    for line in text.split("\n"):
        pattern = find_match(line, INSTRUCTION_PATTERNS)
        if pattern is None:
            kept.append(line)
        else:
            removals.append(Removal(text=line, pattern=pattern))
    # Nothing matched: return the text unchanged (byte for byte).
    if not removals:
        return text, []
    # One marker at the end tells the model the result was filtered.
    kept.append(REMOVED_MARKER)
    return "\n".join(kept), removals
