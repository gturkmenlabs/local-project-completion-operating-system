"""`altai autopilot`: one bounded pass that reports the single next actionable
unit, with opportunities and a policy check folded in.

This does **not** run a loop and it does **not** write code. ALTAI has no model
access and no network access of its own (see `altai/research.py`) — the actual
research, implementation and testing stays exactly where `SKILL.md` already
puts it, with the host agent (Claude Code / Codex). What autopilot adds over
plain `altai next` is: it also loads the top-scored open opportunities and runs
the active task's own text past `policy_engine.classify`, so a single call
tells the host agent whether to (a) proceed with the returned task under
`SKILL.md`'s normal loop, (b) stop and ask the user first, (c) look at
`altai opportunities` because there is no required work left, or (d) fix
whatever is blocking the graph.

Exactly one rescan happens per call (inside `bootstrap`), not per task — a host
agent that calls `autopilot` in its own loop must not pay a full code-graph
rebuild on every iteration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .graph import blocked_tasks, project_phase
from .intelligence.opportunity_finder import load_opportunities
from .orchestrator import bootstrap, next_brief
from .policy_engine import classify

#: Mirrors altai.cli's exit codes for `next`, plus one more: a ready task
#: whose own text tripped a policy category. Kept distinct from EXIT_OK so a
#: shell loop can branch on it without parsing text.
EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_COMPLETE = 4
EXIT_POLICY_HOLD = 5

TOP_OPPORTUNITIES = 3

_INSTRUCTIONS = {
    "complete": (
        "No required work is ready. Confirm with `altai status`; if it reports BITTI, the "
        "declared project is finished. Optionally review `altai opportunities` for further "
        "improvement candidates — each needs an explicit `altai promote <id>` to become work."
    ),
    "blocked": (
        "Nothing is ready. Run `altai status` for the blocked task list and reasons, resolve "
        "or `altai unblock` them, then call autopilot again."
    ),
    "policy_hold": (
        "Do not implement automatically. This task's own text matched a category CLAUDE.md "
        "requires human approval for. Ask the user before proceeding, or `altai skip` it."
    ),
    "proceed": (
        "Follow SKILL.md's normal loop for this task: research, implement, test, then "
        "`altai done`/`fail`/`block`. Autopilot does not do this step itself."
    ),
}


@dataclass(slots=True)
class AutopilotReport:
    task: dict[str, Any] | None
    phase: str
    policy_flags: list[str] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    instruction: str = ""
    exit_code: int = EXIT_OK

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("exit_code")
        return data


def run_autopilot(root: Path, rescan: bool = True) -> AutopilotReport:
    state = bootstrap(root, rescan=rescan)
    brief = next_brief(state)
    opportunities = [c.to_dict() for c in load_opportunities(root)[:TOP_OPPORTUNITIES]]

    if brief is None:
        phase = project_phase(state.tasks)
        blocked = [{"id": t.id, "reason": t.blocked_reason} for t in blocked_tasks(state.tasks)]
        key = "complete" if phase == "DONE" else "blocked"
        return AutopilotReport(
            task=None,
            phase=phase,
            opportunities=opportunities,
            blocked=blocked,
            instruction=_INSTRUCTIONS[key],
            exit_code=EXIT_COMPLETE if phase == "DONE" else EXIT_BLOCKED,
        )

    task_dict = brief["task"]
    flags = classify_task_dict(task_dict)
    key = "policy_hold" if flags else "proceed"
    return AutopilotReport(
        task=brief,
        phase="ACTIVE",
        policy_flags=flags,
        opportunities=opportunities,
        instruction=_INSTRUCTIONS[key],
        exit_code=EXIT_POLICY_HOLD if flags else EXIT_OK,
    )


def classify_task_dict(task_dict: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            task_dict.get("title", ""),
            task_dict.get("description", ""),
            " ".join(task_dict.get("acceptance", [])),
        ]
    )
    return classify(text)
