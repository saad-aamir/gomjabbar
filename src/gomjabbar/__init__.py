"""Gom Jabbar: chaos testing for tool-using AI agents.

What: the top-level package. It holds the version string only.
Why: so `import gomjabbar` works and other modules can report the version.
How: every component lives in a subpackage (proxy, agent, runner, graders, analysis, ...).
"""

# Version reported in run metadata.
__version__ = "0.1.0"
