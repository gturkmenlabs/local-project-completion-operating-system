"""How much `altai run` is allowed to decide on its own.

`policy_engine` classifies a task's text against the stop-and-ask categories.
This module decides what happens *next* — hold the task, or approve it and keep
going. Two levels:

``guarded``
    The historical behaviour. A task whose own text matches a stop-and-ask
    category ends the run with :data:`altai.loop.EXIT_POLICY_HOLD`, and only
    unflagged recommendations are promoted into work.

``full``
    Unattended operation, the default for ``altai run``. Nothing holds: flagged
    tasks are approved automatically, every recommendation is promoted, and the
    host agent CLI is launched with its own approval prompts disabled. Approvals
    are not silent — each one is written to ``.altai/runs/log.md`` and to the
    task's evidence file, so the trail survives the run that produced it.

Removing the gate does not remove responsibility for it. ``full`` gives the
host agent whatever the operator's own account and CLI configuration already
allow; it cannot grant more than that, and it is not a sandbox. Choose it for a
repository whose worst case is a bad commit — one that is version-controlled,
not carrying production credentials, and not wired to deploy on push.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

GUARDED = "guarded"
FULL = "full"
LEVELS = (GUARDED, FULL)

#: Overrides the default level without a flag, for CI or a shell profile.
ENV_LEVEL = "ALTAI_AUTONOMY"


@dataclass(slots=True, frozen=True)
class Autonomy:
    """Immutable autonomy decision for one run."""

    level: str = FULL

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"Unknown autonomy level {self.level!r}. Use one of: {', '.join(LEVELS)}")

    @classmethod
    def from_env(cls, level: str | None = None, env: dict[str, str] | None = None) -> "Autonomy":
        """Explicit *level* wins, then ``ALTAI_AUTONOMY``, then ``full``."""
        source = env if env is not None else os.environ
        raw = (level or source.get(ENV_LEVEL) or FULL).strip().lower()
        return cls(level=raw)

    @property
    def unattended(self) -> bool:
        return self.level == FULL

    def holds(self, flags: list[str]) -> bool:
        """True when *flags* must stop the run instead of being approved."""
        return bool(flags) and not self.unattended

    @property
    def promotes_flagged(self) -> bool:
        """Whether a flagged recommendation may still become a task."""
        return self.unattended

    @property
    def bypass_agent_approvals(self) -> bool:
        """Whether the host agent CLI is launched with prompts disabled."""
        return self.unattended

    def approval_note(self, task_id: str, flags: list[str]) -> str:
        """The audit line recorded when this policy approves a flagged task."""
        return (
            f"auto-approved {task_id} under '{self.level}' autonomy despite policy "
            f"flags: {', '.join(flags)}"
        )
