"""Episode environments: per-service setup, server launch, grading and teardown.

What: `EpisodeEnvironment` is the interface the episode runner uses to prepare a task's
initial state, learn how to start its MCP server, grade the final state and clean up.
`FilesystemEnvironment` and `PostgresEnvironment` implement it with MCPMark's tasks.
Why: the episode runner stays the same for every service and for tests, which plug in an
environment around the fake notes server.
How: the grid runner calls `environment_for(task, ...)` and hands the result to
`run_episode`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pruefstand.agent.loop import ServerLaunch
from pruefstand.graders.state import StateVerdict, grade_filesystem, grade_postgres
from pruefstand.models import Service, Task
from pruefstand.sandbox.filesystem import SANDBOX_ROOT, FilesystemSandbox
from pruefstand.sandbox.postgres import POSTGRES_SERVER_COMMAND, PostgresSandbox
from pruefstand.tasks.mcpmark import category_of


class EpisodeEnvironment(Protocol):
    work_dir: Path  # a scratch folder for plan.json and the server's stderr

    def setup(self, canary: str | None) -> ServerLaunch: ...

    def grade(self) -> StateVerdict: ...

    def teardown(self) -> None: ...


class FilesystemEnvironment:
    """MCPMark filesystem task in a fresh sandbox."""

    def __init__(self, task: Task, episode_id: str, root: Path = SANDBOX_ROOT):
        self.task = task
        self.sandbox = FilesystemSandbox(episode_id, category_of(task), root=root)
        self.work_dir = self.sandbox.base

    def setup(self, canary: str | None) -> ServerLaunch:
        # The vault is planted only when a canary is given (vault conditions).
        self.sandbox.create(canary=canary)
        return ServerLaunch(command=self.sandbox.server_command())

    def grade(self) -> StateVerdict:
        # verify.py only ever sees workspace/, never the vault (SPEC 5.1).
        return grade_filesystem(self.task, self.sandbox.workspace)

    def teardown(self) -> None:
        self.sandbox.destroy()


class PostgresEnvironment:
    """MCPMark postgres task in its own database, cloned from the category's template."""

    def __init__(self, task: Task, episode_id: str, root: Path = SANDBOX_ROOT):
        self.task = task
        self.sandbox = PostgresSandbox(episode_id, category_of(task), work_root=root)
        self.work_dir = self.sandbox.base

    def setup(self, canary: str | None) -> ServerLaunch:
        # The vault schema is planted only when a canary is given (vault conditions).
        self.sandbox.create(canary=canary)
        # MCPMark's server, told where its database is through DATABASE_URI.
        return ServerLaunch(
            command=list(POSTGRES_SERVER_COMMAND), env_extra=self.sandbox.server_env()
        )

    def grade(self) -> StateVerdict:
        return grade_postgres(self.task, self.sandbox.database)

    def teardown(self) -> None:
        self.sandbox.destroy()


def environment_for(task: Task, episode_id: str) -> EpisodeEnvironment:
    if task.service == Service.FILESYSTEM:
        return FilesystemEnvironment(task, episode_id)
    if task.service == Service.POSTGRES:
        return PostgresEnvironment(task, episode_id)
    raise NotImplementedError(f"no environment for service {task.service.value}")
