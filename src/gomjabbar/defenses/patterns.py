"""Instruction-like text patterns shared by the sanitizer and the description scan.

What: two lists of compiled regular expressions. `INSTRUCTION_PATTERNS` is the family both
defenses use: text that tries to give the agent orders (ignore your instructions, you must,
fake system or assistant turns, requests to hand over a key, claims of authority).
`DESCRIPTION_PATTERNS` adds phrases that are suspicious inside a tool description in
particular ("mandatory", "before finishing any task", "include ... in the arguments").
`find_match(text, patterns)` returns the first pattern that matches, or None.
Why: a host application cannot tell trusted from untrusted text by its source alone, so it
looks for the shape of an instruction (SPEC 5.7). Keeping the patterns in one module means
both defenses agree on what counts as an instruction, and the false-positive measurement
tests exactly the patterns the defenses use.
How: defenses/sanitizer.py checks each line of a tool result against INSTRUCTION_PATTERNS;
defenses/description_scan.py checks each sentence of a tool description against both lists.

These patterns were written with the repo's 8 payloads in view, so how well they stop those
payloads is an upper bound. They are frozen at the git tag `defense-patterns-v1` and then
tested on held-out payloads (DEVIATIONS.md, 2026-10-04). Do not edit them for that experiment.
"""

from __future__ import annotations

import re

# Case-insensitive throughout: attackers shout ("SYSTEM NOTICE", "<IMPORTANT>").
_FLAGS = re.IGNORECASE

# Words that name a secret the agent should never hand on.
_SECRET = r"(api[\s_-]?keys?|secret\s+keys?|secrets?|credentials?|passwords?|access[\s_-]?tokens?|admin[\s_-]?tokens?)"

# The shared family: instruction-like text, wherever it appears.
INSTRUCTION_PATTERNS: list[re.Pattern[str]] = [
    # "ignore all previous instructions", "ignore prior instructions"
    re.compile(
        r"\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+instructions",
        _FLAGS,
    ),
    # "The previous instructions are outdated."
    re.compile(
        r"\b(previous|prior|earlier|original)\s+instructions\s+(are|were)\s+(outdated|obsolete|void|cancelled|replaced)",
        _FLAGS,
    ),
    # A direct order to the model.
    re.compile(r"\byou\s+must\b", _FLAGS),
    # Pseudo-markup used to make hidden text look important: <IMPORTANT>, </important>.
    re.compile(r"<\s*/?\s*important\s*>", _FLAGS),
    # Fake conversation turns: "system:", "assistant:", also inside "<!-- assistant: ...".
    re.compile(r"(^|[\s<!\-\[(>])(system|assistant)\s*:", _FLAGS),
    # Fake system messages: "SYSTEM NOTICE", "system override".
    re.compile(r"\bsystem\s+(notice|message|override|instruction)s?\b", _FLAGS),
    # "Before using this tool", "before calling any tool".
    re.compile(r"\bbefore\s+(using|calling)\s+(this|any|the)\s+tools?\b", _FLAGS),
    # "Before continuing, ..." addressed to the agent.
    re.compile(r"\bbefore\s+continuing\b", _FLAGS),
    # "Your new task is ..."
    re.compile(r"\byour\s+new\s+(task|instructions?|goal|objective)\b", _FLAGS),
    # Asking the agent to hide something from its user.
    re.compile(r"\bdo\s+not\s+(mention|tell|reveal|disclose|inform)\b", _FLAGS),
    # Asking the agent to read, pass on or store a secret.
    re.compile(
        r"\b(read|copy|save|send|include|repeat|set|put|store|reveal|share|provide|paste)\b[^.\n]{0,80}\b"
        + _SECRET
        + r"\b",
        _FLAGS,
    ),
    # Asking for a secret as a value: "set to the value of the vault API key".
    re.compile(r"\bvalue\s+of\s+the\s+[^.\n]{0,40}" + _SECRET, _FLAGS),
    # Asking the agent to redo a call with something added.
    re.compile(r"\brepeat\s+(your|the)\s+(last|previous)\s+(tool\s+)?call", _FLAGS),
    # Claimed authority: "Note from the maintainers", "Reminder from the workspace owner".
    re.compile(
        r"\b(note|message|reminder|notice|request)\s+from\s+the\s+(workspace\s+|repository\s+|system\s+)?"
        r"(maintainers?|owners?|administrators?|admins?|developers?)\b",
        _FLAGS,
    ),
    # Scope creep tacked on to the real task.
    re.compile(r"\bwhile\s+you\s+are\s+at\s+it\b", _FLAGS),
]

# Extra phrases for tool descriptions: a real description says what a tool does, it does not
# set conditions on the agent's whole task or threaten failure.
DESCRIPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bmandatory\b", _FLAGS),
    # "Before finishing any task", "after completing any requested change".
    re.compile(
        r"\bbefore\s+(finishing|completing|ending)\s+(any|the|this|each|every|your)\b", _FLAGS
    ),
    re.compile(r"\bafter\s+(finishing|completing)\s+(any|the|each|every|your)\b", _FLAGS),
    # "include its value in the arguments of this call"
    re.compile(r"\binclude\b[^.\n]{0,80}\bin\s+the\s+(arguments?|parameters?)\b", _FLAGS),
    # "This is required for compliance logging."
    re.compile(r"\brequired\s+for\s+(compliance|audit|auditing|logging|security)\b", _FLAGS),
    # Threats: "Tasks that skip this check are rejected by the system", "Sync will fail without it".
    re.compile(r"\b(rejected|blocked|refused)\s+by\s+the\s+system\b", _FLAGS),
    re.compile(r"\b(fails?|will\s+fail)\s+without\s+it\b", _FLAGS),
]


def find_match(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    """The source of the first pattern that matches `text`, or None if none does."""
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return None
