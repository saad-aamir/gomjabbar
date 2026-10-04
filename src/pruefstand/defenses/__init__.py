"""Host-side defenses the agent loop can switch on per run (SPEC 5.7).

pinning: freeze tool definitions after the first listing. sanitizer: remove instruction-like
lines from tool results. description_scan: remove instruction-like sentences from tool
descriptions at session start, hiding mostly-poisoned tools. `KNOWN_DEFENSES` lists the
names a config may use in `defenses`.
"""

KNOWN_DEFENSES = ("pinning", "sanitizer", "description_scan")
