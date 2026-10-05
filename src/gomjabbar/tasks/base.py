"""The TaskLoader protocol: anything that can turn task ids into Task objects.

What: a tiny interface with `load(task_id)` and `discover(service, suite)`.
Why: MCPMark is the first task source; custom YAML tasks (P2) will be another. The runner
only depends on this protocol, not on MCPMark.
How: tasks/mcpmark.py implements it; the runner calls `load` for each id in the suite file.
"""

from typing import Protocol

from gomjabbar.models import Service, Task


class TaskLoader(Protocol):
    def load(self, task_id: str) -> Task: ...

    def discover(self, service: Service, suite: str) -> list[Task]: ...
