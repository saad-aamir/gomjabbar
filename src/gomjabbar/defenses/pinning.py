"""Pinning defense: freeze the tool definitions the session started with.

What: `ToolPin` remembers a hash of each tool's name, description and input schema from the
first listing. `ToolPin.check(new_tools)` compares a later listing with it and always returns
the original definitions, plus one `PinAction` per tool that changed, appeared or vanished.
Why: a "rug pull" serves clean descriptions until the agent trusts a server, then swaps in
poisoned ones mid-session. A host that pins what it first approved never shows the model the
new text (SPEC 5.7). It cannot help against a description poisoned from the first listing;
that is description_scan's job.
How: agent/loop.py creates a ToolPin on the first `list_tools` when "pinning" is in the
episode's defenses. On every later listing (after `notifications/tools/list_changed`) it
shows the model `check(...)`'s result and writes one `defense_action` per change.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass


def tool_hash(tool: dict) -> str:
    """sha256 of a tool's name, description and parameters as canonical JSON."""
    function = tool["function"]
    data = {
        "name": function["name"],
        "description": function.get("description") or "",
        "parameters": function.get("parameters") or {},
    }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class PinAction:
    """One difference between a later listing and the pinned one."""

    tool: str
    change: str  # "changed", "added" or "removed"


class ToolPin:
    """The pinned tool list of one session."""

    def __init__(self, tools: list[dict]):
        # Keep a private copy so later edits to the caller's list cannot change the pin.
        self.tools = copy.deepcopy(tools)
        self.hashes = {t["function"]["name"]: tool_hash(t) for t in self.tools}

    def check(self, new_tools: list[dict]) -> tuple[list[dict], list[PinAction]]:
        """Compare a new listing with the pin; return the pinned tools and the differences."""
        new_hashes = {t["function"]["name"]: tool_hash(t) for t in new_tools}
        actions = []
        # Sorted so the order of actions is the same every time.
        for name in sorted(set(self.hashes) | set(new_hashes)):
            if name not in new_hashes:
                actions.append(PinAction(name, "removed"))
            elif name not in self.hashes:
                actions.append(PinAction(name, "added"))
            elif new_hashes[name] != self.hashes[name]:
                actions.append(PinAction(name, "changed"))
        # Whatever changed, the model keeps seeing the original definitions.
        return copy.deepcopy(self.tools), actions
