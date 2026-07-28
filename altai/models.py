from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4

#: Task IDs end up in file paths (`.altai/evidence/<id>.md`), so they must not be
#: able to escape the workspace.
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

#: How many times a task may be unblocked before a human has to intervene.
MAX_UNBLOCKS = 2


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_RE.match(task_id or "") or ".." in task_id:
        raise ValueError(
            f"Invalid task id {task_id!r}. Use letters, digits, dot, dash or underscore "
            "(max 80 chars, no path segments)."
        )
    return task_id


class TaskStatus(str, Enum):
    """Lifecycle of a single unit of work.

    Plain ``(str, Enum)`` instead of ``enum.StrEnum`` so the package runs on
    Python 3.10, which is still the default interpreter on many systems.
    """

    UNKNOWN = "unknown"
    RESEARCHED = "researched"
    PLANNED = "planned"
    CODING = "coding"
    TESTING = "testing"
    VERIFIED = "verified"
    DONE = "done"
    BLOCKED = "blocked"
    #: Consciously not doing this. Counts as settled so a permanently blocked
    #: task cannot make completion unreachable.
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


#: Statuses meaning "this task will not be picked up again".
TERMINAL = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.SKIPPED})
#: Statuses that satisfy a dependency and count towards completion.
SATISFIED = frozenset({TaskStatus.DONE, TaskStatus.SKIPPED})
#: Settled outcomes that must never be reverted by inference.
SETTLED = SATISFIED


@dataclass(slots=True)
class Task:
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.UNKNOWN
    dependencies: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    evidence: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    #: True when the block was inferred by the graph rather than set by a human.
    #: Auto blocks are re-evaluated on every run; manual blocks are sticky.
    blocked_auto: bool = False
    #: Status held before an auto block, so clearing it restores real progress
    #: instead of flattening everything back to UNKNOWN.
    status_before_block: str = ""
    notes: str = ""
    #: How many times this task has been unblocked and retried from scratch.
    unblocks: int = 0
    #: True for tasks derived from a source scan (TODO/FIXME markers).
    discovered: bool = False

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        """Tolerant loader: a hand-mangled state file must raise a clean error,
        not an ``AttributeError`` three call frames deep."""
        if not isinstance(data, dict):
            raise ValueError(f"Task entry must be an object, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        try:
            payload["status"] = TaskStatus(payload.get("status", TaskStatus.UNKNOWN))
        except ValueError:
            payload["status"] = TaskStatus.UNKNOWN
        for name in ("dependencies", "acceptance", "evidence"):
            value = payload.get(name)
            if value is None:
                continue
            if not isinstance(value, list):
                raise ValueError(f"Task '{payload.get('id', '?')}' field '{name}' must be a list")
            payload[name] = [str(item) for item in value]
        for name in ("attempts", "max_attempts", "unblocks"):
            if name in payload:
                try:
                    payload[name] = int(payload[name])
                except (TypeError, ValueError):
                    raise ValueError(f"Task '{payload.get('id', '?')}' field '{name}' must be an int")
        payload.setdefault("id", "")
        payload.setdefault("title", "")
        return cls(**payload)


@dataclass(slots=True)
class ProjectState:
    root: Path
    name: str
    stack: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root": str(self.root),
            "name": self.name,
            "stack": self.stack,
            "goals": self.goals,
            "risks": self.risks,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], root: Path | None = None) -> "ProjectState":
        def string_list(key: str) -> list[str]:
            value = data.get(key, [])
            if not isinstance(value, list):
                raise ValueError(f"State field '{key}' must be a list")
            return [str(item) for item in value]

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError("State field 'tasks' must be a list")
        try:
            schema_version = int(data.get("schema_version", 1))
        except (TypeError, ValueError):
            schema_version = 1
        return cls(
            root=Path(root if root is not None else data.get("root", ".")),
            name=str(data.get("name", "")),
            stack=string_list("stack"),
            goals=string_list("goals"),
            risks=string_list("risks"),
            tasks=[Task.from_dict(item) for item in tasks],
            schema_version=schema_version,
        )
