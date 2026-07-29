"""`altai autopilot`: one bounded pass that reports the single next actionable
unit, with opportunities and a policy check folded in.

This does **not** run a loop and it does **not** write application code. With
the opt-in ``design`` flag it writes deterministic pre-code design artifacts;
the default path remains report-only. ALTAI has no model access and no network
access of its own (see `altai.research`) — the actual benchmark research,
implementation and testing stays exactly where ``SKILL.md`` already puts it,
with the host agent (Claude Code / Codex).

Exactly one rescan happens per call (inside `bootstrap`), not per task — a host
agent that calls `autopilot` in its own loop must not pay a full code-graph
rebuild on every iteration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .design import generate_design_plan
from .graph import blocked_tasks, project_phase
from .intelligence.opportunity_finder import load_opportunities
from .orchestrator import bootstrap, next_brief, promote_opportunity
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
    applied_recommendations: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    design: dict[str, str] = field(default_factory=dict)
    instruction: str = ""
    exit_code: int = EXIT_OK

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("exit_code")
        return data


def run_autopilot(
    root: Path,
    rescan: bool = True,
    design: bool = False,
    apply_recommendations: bool = False,
) -> AutopilotReport:
    state = bootstrap(root, rescan=rescan)
    design_paths = (
        {name: str(path) for name, path in generate_design_plan(root).items()} if design else {}
    )
    applied_recommendations = []
    if apply_recommendations:
        for candidate in load_opportunities(root):
            candidate_dict = candidate.to_dict()
            if classify_task_dict(candidate_dict):
                continue
            state, _ = promote_opportunity(root, candidate.id)
            applied_recommendations.append(candidate_dict)

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
            applied_recommendations=applied_recommendations,
            blocked=blocked,
            design=design_paths,
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
        applied_recommendations=applied_recommendations,
        design=design_paths,
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
