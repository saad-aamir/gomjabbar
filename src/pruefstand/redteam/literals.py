"""The literal check for paraphrases: every exact detail of a task must survive rewording.

What: `extract_literals(text)` finds the details an agent must reproduce exactly (file paths
and names, numbers, quoted strings, code spans, SQL identifiers, table cells), and
`missing_literals(original, paraphrase)` lists those absent from a paraphrase.
Why: a paraphrase that changes "split_01.txt" to "the first split file", or 100 to "a
hundred", is a different task, so its result would not measure robustness to wording
(SPEC 5.6, check 1). This check is deterministic and cheap, so it runs before the LLM
equivalence check.
How: plain regular expressions, one per kind of literal, each explained where it is
defined. redteam/paraphrase.py calls `missing_literals` on every candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Inline code in Markdown: `split_01.txt`, `exec_department_summary`. The content counts.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# Text in double quotes, straight or curly: "MelodyMart", “Hello world”.
_DOUBLE_QUOTED = re.compile(r"\"([^\"\n]+)\"|“([^”\n]+)”")
# Text in single quotes that is not an apostrophe: '9999-01-01', 'USA'. The quote must not
# follow a letter (manager's) and the content must not end in a letter followed by a quote
# inside a word.
_SINGLE_QUOTED = re.compile(r"(?<![A-Za-z0-9])'([^'\n]+)'(?![A-Za-z0-9])")
# File names with an extension (large_file.txt, .jpg) and paths with slashes (a/b/c.md).
_FILE = re.compile(r"(?<![\w/.-])(?:[\w.-]*/)*[\w-]*\.[A-Za-z][A-Za-z0-9]{0,4}(?![\w])")
_PATH = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+")
# Numbers, including decimals and dates written with dashes (9999-01-01 is kept whole by
# the quoted-string rule; its parts are still numbers here).
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
# Markdown list numbering ("1. ", "  2) ") at the start of a line is structure, not content.
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
# SQL-style identifiers: snake_case (to_date, dept_emp) and CamelCase (CustomerId, FirstName).
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")
# A Markdown table row: | a | b |. Every non-empty cell that is not a separator counts.
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Literal:
    """One detail that must appear unchanged in a paraphrase."""

    kind: str  # "code", "quoted", "file", "path", "number", "identifier", "table_cell"
    text: str


def extract_literals(text: str) -> list[Literal]:
    """Every literal in the text, without duplicates, in order of first appearance."""
    found: list[Literal] = []

    def add(kind: str, value: str) -> None:
        value = value.strip()
        if value and all(item.text != value for item in found):
            found.append(Literal(kind, value))

    for match in _CODE_SPAN.finditer(text):
        add("code", match.group(1))
    # Punctuation that ends the sentence inside the quotes ("MelodyMart,") is not part of it.
    for match in _DOUBLE_QUOTED.finditer(text):
        add("quoted", (match.group(1) or match.group(2)).rstrip(",.;:"))
    for match in _SINGLE_QUOTED.finditer(text):
        add("quoted", match.group(1).rstrip(",.;:"))
    for match in _TABLE_ROW.finditer(text):
        for cell in match.group(1).split("|"):
            # Separator rows (|---|:--:|) are layout, not content.
            if cell.strip() and not re.fullmatch(r"\s*:?-{2,}:?\s*", cell):
                add("table_cell", cell)
    for match in _PATH.finditer(text):
        add("path", match.group(0))
    for match in _FILE.finditer(text):
        add("file", match.group(0))
    for match in _SNAKE.finditer(text):
        add("identifier", match.group(0))
    for match in _CAMEL.finditer(text):
        add("identifier", match.group(0))
    # Numbers last, ignoring list numbering, which a paraphrase may restructure freely.
    without_markers = _LIST_MARKER.sub(" ", text)
    for match in _NUMBER.finditer(without_markers):
        add("number", match.group(0))
    return found


def _present(literal: Literal, text: str) -> bool:
    """True if the literal appears in the text (numbers only as whole numbers)."""
    if literal.kind == "number":
        # "3" must not be satisfied by "30" or "3.5", nor by list numbering ("3. Save"),
        # which extraction ignores too.
        pattern = rf"(?<![\d.]){re.escape(literal.text)}(?![\d]|\.\d)"
        return re.search(pattern, _LIST_MARKER.sub(" ", text)) is not None
    return literal.text in text


def missing_literals(original: str, paraphrase: str) -> list[Literal]:
    """The literals of the original that the paraphrase lost (empty list: check passed)."""
    return [lit for lit in extract_literals(original) if not _present(lit, paraphrase)]
