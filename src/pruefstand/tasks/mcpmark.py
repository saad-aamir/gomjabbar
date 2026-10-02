"""Adapter from the vendored MCPMark task folders to Prüfstand's Task model.

What: finds MCPMark tasks, builds the exact prompt MCPMark would give the agent, and loads
suite files (lists of task ids).
Why: we reuse MCPMark's descriptions, initial states and verifiers, but not its agent runner
(SPEC 2.1). This module is the one place that knows MCPMark's folder layout.
How: a task id is "<service>/<suite>/<category>/<task>", matching the folder path under
vendor/mcpmark/tasks. Details are in docs/notes/mcpmark-interface.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from pruefstand.models import Service, Task
from pruefstand.paths import MCPMARK_ROOT

# The text MCPMark appends to every description (src/base/task_manager.py:372 and
# src/mcp_services/postgres/postgres_task_manager.py:112), copied character for character.
PROMPT_SUFFIX = {
    Service.FILESYSTEM: (
        "\n\nNote: Based on your understanding, solve the task all at once by yourself, "
        "don't ask for my opinions on anything."
    ),
    Service.POSTGRES: (
        "\n\nNote: Use PostgreSQL MCP tools to complete this task. "
        "The database connection is already configured."
    ),
}

# MCPMark's filesystem server command (src/agents/base_agent.py:188); the allowed roots
# are appended as positional arguments.
FILESYSTEM_SERVER_COMMAND = ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.12.18"]


class MCPMarkTasks:
    """TaskLoader for the vendored MCPMark copy."""

    def __init__(self, root: Path = MCPMARK_ROOT):
        self.tasks_dir = root / "tasks"

    def load(self, task_id: str) -> Task:
        """Load one task by id, e.g. "filesystem/easy/file_property/largest_rename"."""
        parts = task_id.split("/")
        if len(parts) != 4:
            raise ValueError(f"task id must be service/suite/category/task, got {task_id!r}")
        service = Service(parts[0])
        folder = self.tasks_dir.joinpath(*parts)
        description_path = folder / "description.md"
        if not description_path.exists():
            raise FileNotFoundError(f"no MCPMark task at {folder}")
        meta_path = folder / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        # The prompt is description.md plus MCPMark's fixed suffix for the service.
        description = description_path.read_text(encoding="utf-8") + PROMPT_SUFFIX[service]
        return Task(
            id=task_id, service=service, description=description, source_dir=folder, meta=meta
        )

    def discover(self, service: Service, suite: str) -> list[Task]:
        """Every task of a service and suite, sorted by (category, task) like MCPMark."""
        suite_dir = self.tasks_dir / service.value / suite
        ids = [
            f"{service.value}/{suite}/{category.name}/{task.name}"
            for category in sorted(p for p in suite_dir.iterdir() if p.is_dir())
            for task in sorted(p for p in category.iterdir() if p.is_dir())
            if (task / "description.md").exists()
        ]
        return [self.load(task_id) for task_id in ids]


def category_of(task: Task) -> str:
    """The MCPMark category, which names the initial state (zip or template database)."""
    # meta.json's category_id is what MCPMark uses; the folder name is the fallback.
    return task.meta.get("category_id") or task.id.split("/")[2]


def read_suite(path: Path | str) -> list[str]:
    """Task ids from a suite file: one per line, '#' starts a comment, blanks ignored."""
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            ids.append(text)
    return ids
