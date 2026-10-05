"""Description scan defense: strip instruction-like sentences from tool descriptions.

What: `scan_tools(tools)` takes the tool list (OpenAI format, as the model would see it),
removes every sentence of a tool description or parameter description that matches the
instruction patterns, and hides a tool completely when the removals leave its description
empty or take out more than half of its text. It returns the cleaned list and one
`ScanAction` per removed sentence or hidden tool.
Why: tool poisoning hides orders in a tool's description, which the model reads as trusted
documentation. Pinning cannot help when the description is poisoned from the very first
listing, and the sanitizer only sees tool results. A host can instead check descriptions
once, when the session starts. A tool whose text is mostly orders (a shadow tool) is safer
hidden than shown half-cleaned. Added in M4 at Saad's request (DEVIATIONS.md, 2026-10-04).
How: agent/loop.py calls `scan_tools` after `list_tools` when "description_scan" is in the
episode's defenses, shows the model the cleaned list, writes one `defense_action` trace event
per action, and refuses calls to hidden tools. scripts/defense_false_positives.py runs it over
the clean MCPMark tool lists.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from gomjabbar.defenses.patterns import (
    DESCRIPTION_PATTERNS,
    INSTRUCTION_PATTERNS,
    find_match,
)

# Both families apply to descriptions.
ALL_PATTERNS = INSTRUCTION_PATTERNS + DESCRIPTION_PATTERNS
# A block wrapped in <IMPORTANT> ... </IMPORTANT> is removed as one unit, whatever is inside:
# the tags exist only to make hidden text look authoritative.
_IMPORTANT_BLOCK = re.compile(
    r"<\s*important\s*>.*?<\s*/\s*important\s*>", re.IGNORECASE | re.DOTALL
)
# Sentence boundary: end punctuation followed by whitespace, or a line break. Requiring the
# whitespace keeps "vault/.env" and "e.g." from splitting a sentence in the middle.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n")
# Hide the tool when more than this share of its text was removed.
HIDE_FRACTION = 0.5


@dataclass
class ScanAction:
    """One thing the scan did: removed a sentence, or hid a whole tool."""

    tool: str
    action: str  # "removed_sentence" or "hidden_tool"
    where: str  # "description" or "param:<name>" (empty for hidden_tool)
    text: str  # the removed text, or the reason for hiding
    pattern: str = ""  # the pattern that matched (for removals)


def _sentences(text: str) -> list[tuple[int, int]]:
    """Start and end offsets of each sentence in `text`."""
    spans = []
    start = 0
    for boundary in _BOUNDARY.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    # Empty stretches between two boundaries are not sentences.
    return [(a, b) for a, b in spans if text[a:b].strip()]


def clean_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove <IMPORTANT> blocks and matching sentences from one text.

    Returns the cleaned text and a list of (removed text, pattern). With nothing removed the
    original text comes back unchanged.
    """
    removed: list[tuple[str, str]] = []
    # 1. Whole <IMPORTANT> blocks first, so their sentences are not judged one by one.
    for block in _IMPORTANT_BLOCK.findall(text):
        removed.append((block, _IMPORTANT_BLOCK.pattern))
    working = _IMPORTANT_BLOCK.sub(" ", text)
    # 2. Then every sentence that looks like an instruction.
    cut: list[tuple[int, int]] = []
    for start, end in _sentences(working):
        pattern = find_match(working[start:end], ALL_PATTERNS)
        if pattern is not None:
            removed.append((working[start:end].strip(), pattern))
            cut.append((start, end))
    if not removed:
        return text, []
    # Rebuild the text without the cut sentences (from the end, so offsets stay valid).
    for start, end in reversed(cut):
        working = working[:start] + working[end:]
    # Tidy the whitespace the removals left behind.
    working = re.sub(r"[ \t]{2,}", " ", working)
    working = re.sub(r"\n{3,}", "\n\n", working).strip()
    return working, removed


def _param_descriptions(schema: dict) -> list[tuple[str, dict]]:
    """Every parameter dict that has a description, with a readable name, nested included."""
    found = []

    def walk(node: dict, path: str) -> None:
        for name, prop in (node.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            where = f"{path}{name}"
            if isinstance(prop.get("description"), str):
                found.append((where, prop))
            walk(prop, where + ".")
            # Array items can carry their own description and properties.
            if isinstance(prop.get("items"), dict):
                items = prop["items"]
                if isinstance(items.get("description"), str):
                    found.append((where + "[]", items))
                walk(items, where + "[].")

    walk(schema or {}, "")
    return found


def scan_tools(tools: list[dict]) -> tuple[list[dict], list[ScanAction]]:
    """Clean every tool's description and parameter descriptions; hide mostly-poisoned tools.

    `tools` is the OpenAI-format list ({"type": "function", "function": {...}}). The input is
    not changed; a cleaned deep copy is returned.
    """
    kept: list[dict] = []
    actions: list[ScanAction] = []
    for tool in tools:
        tool = copy.deepcopy(tool)
        function = tool["function"]
        name = function["name"]
        tool_actions: list[ScanAction] = []
        # Characters of description text before and after cleaning, over the tool's
        # description and all its parameter descriptions together.
        total_chars = 0
        removed_chars = 0

        # The tool's own description.
        original = function.get("description") or ""
        total_chars += len(original)
        cleaned, removed = clean_text(original)
        function["description"] = cleaned
        for text, pattern in removed:
            removed_chars += len(text)
            tool_actions.append(ScanAction(name, "removed_sentence", "description", text, pattern))

        # Every parameter description, nested ones included.
        for where, prop in _param_descriptions(function.get("parameters") or {}):
            original_param = prop["description"]
            total_chars += len(original_param)
            cleaned_param, removed = clean_text(original_param)
            prop["description"] = cleaned_param
            for text, pattern in removed:
                removed_chars += len(text)
                tool_actions.append(
                    ScanAction(name, "removed_sentence", f"param:{where}", text, pattern)
                )

        actions.extend(tool_actions)
        # Hide the tool if its description was emptied, or most of its text was orders.
        emptied = bool(original.strip()) and not cleaned.strip()
        mostly_removed = total_chars > 0 and removed_chars > HIDE_FRACTION * total_chars
        if tool_actions and (emptied or mostly_removed):
            reason = "description empty after removal" if emptied else "over half removed"
            actions.append(ScanAction(name, "hidden_tool", "", reason))
            continue
        kept.append(tool)
    return kept, actions
