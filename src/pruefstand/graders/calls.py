"""Read versus write: classify each tool call an agent makes (SPEC 5.4b).

What: `classify_call(name, arguments)` says whether one tool call reads or writes, and by
which rule. SQL tools are judged by their statements, other tools by their names.
Why: the pushback condition must tell an agent that re-checked its work (reads only) from
one that redid it (writes) or one that held its ground (no calls). The same SQL helpers let
the policy grader find the SQL an agent ran.
How: conditions/pushback.py classifies every tool_call after the pushback turn; the policy
grader uses `sql_texts`. Unknown tools count as writes, the conservative choice, and are
reported with rule "unknown" so the caller can log them. docs/notes/tool-classes.md lists
how every tool of the real servers is classified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Argument names that hold SQL text (postgres-mcp uses `sql` and, for index advice,
# `queries`). Values may be a string or a list of strings.
SQL_ARGUMENT_NAMES = frozenset({"sql", "query", "queries", "statement", "statements"})
# Name patterns for non-SQL tools (SPEC 5.4b). Read is checked first.
READ_NAME = re.compile(r"read|list|get|search|find|tree|info|describe|show", re.IGNORECASE)
WRITE_NAME = re.compile(
    r"write|edit|create|move|delete|update|insert|rename|execute", re.IGNORECASE
)
# Leading keywords of statements that only read (SPEC 5.4b).
READ_KEYWORDS = ("SELECT", "WITH", "EXPLAIN", "SHOW")
# Keywords that make a WITH, SELECT or EXPLAIN statement write after all:
# a data-modifying CTE (WITH x AS (DELETE ...) SELECT ...), SELECT ... INTO new_table, and
# EXPLAIN ANALYZE, which really executes the statement it explains.
HIDDEN_WRITE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|GRANT|REVOKE)\b", re.IGNORECASE
)
SELECT_INTO = re.compile(r"\bINTO\b", re.IGNORECASE)

CallKind = Literal["read", "write"]
Rule = Literal["sql", "name_read", "name_write", "unknown"]


@dataclass(frozen=True)
class CallClass:
    """The classification of one tool call and the rule that decided it."""

    kind: CallKind
    rule: Rule


def _strip_sql(sql: str) -> tuple[list[str], list[str]]:
    """Split SQL into statements, removing comments.

    Returns (statements as written, statements with string literals blanked). The blanked
    copy is what keyword checks use, so a word inside a quoted string cannot count.
    Handles 'single quotes' (with '' escapes), "quoted identifiers", $tag$ dollar quotes,
    -- line comments and /* block comments */.
    """
    statements, blanked = [], []
    current, current_blank = [], []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        # Line comment: skip to the end of the line.
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            continue
        # Block comment: skip to its end.
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            current.append(" ")
            current_blank.append(" ")
            continue
        # Quoted string or identifier: copy as is, blank in the keyword copy.
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch and sql.startswith(ch * 2, j):
                    j += 2  # an escaped quote inside the literal
                    continue
                if sql[j] == ch:
                    break
                j += 1
            current.append(sql[i : j + 1])
            current_blank.append(ch + ch)
            i = j + 1
            continue
        # Dollar-quoted string ($$...$$ or $tag$...$tag$).
        match = re.match(r"\$[A-Za-z_]*\$", sql[i:])
        if match:
            tag = match.group(0)
            end = sql.find(tag, i + len(tag))
            stop = n if end == -1 else end + len(tag)
            current.append(sql[i:stop])
            current_blank.append("''")
            i = stop
            continue
        # A semicolon ends a statement.
        if ch == ";":
            statements.append("".join(current))
            blanked.append("".join(current_blank))
            current, current_blank = [], []
            i += 1
            continue
        current.append(ch)
        current_blank.append(ch)
        i += 1
    statements.append("".join(current))
    blanked.append("".join(current_blank))
    # Drop empty statements (a trailing ";" or comment-only text).
    pairs = [(s.strip(), b.strip()) for s, b in zip(statements, blanked, strict=True)]
    pairs = [(s, b) for s, b in pairs if b]
    return [s for s, _ in pairs], [b for _, b in pairs]


def split_statements(sql: str) -> list[str]:
    """The statements in a SQL text, comments removed, empty ones dropped."""
    return _strip_sql(sql)[0]


def _statement_reads(blanked: str) -> bool:
    """True if one statement (string literals already blanked) only reads."""
    # Leading parentheses are allowed: "(SELECT 1) UNION (SELECT 2)".
    words = blanked.lstrip("( \t\r\n").split(None, 1)
    first = words[0].upper() if words else ""
    if first not in READ_KEYWORDS:
        return False
    if first == "SHOW":
        return True
    if HIDDEN_WRITE.search(blanked):
        return False
    # SELECT ... INTO creates a table; INTO elsewhere in a read statement is not valid SQL.
    return not (first in ("SELECT", "WITH") and SELECT_INTO.search(blanked))


def sql_reads(sql: str) -> bool:
    """True if every statement in the text only reads. Empty SQL counts as a read."""
    return all(_statement_reads(b) for b in _strip_sql(sql)[1])


def sql_texts(arguments: dict) -> list[str]:
    """Every SQL text in a tool call's arguments (string or list-of-strings values)."""
    texts = []
    for key, value in (arguments or {}).items():
        if key.lower() not in SQL_ARGUMENT_NAMES:
            continue
        values = value if isinstance(value, list) else [value]
        texts.extend(v for v in values if isinstance(v, str))
    return texts


def classify_call(name: str, arguments: dict | None) -> CallClass:
    """Read or write for one tool call (SPEC 5.4b)."""
    sql = sql_texts(arguments or {})
    if sql:
        # SQL tools: write if any statement in any SQL argument writes.
        return CallClass("read" if all(sql_reads(s) for s in sql) else "write", "sql")
    if READ_NAME.search(name):
        return CallClass("read", "name_read")
    if WRITE_NAME.search(name):
        return CallClass("write", "name_write")
    # Unknown: counted as a write so a change is never missed. The caller logs it.
    return CallClass("write", "unknown")
