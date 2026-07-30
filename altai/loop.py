"""`altai run`: one command that takes a repository from wherever it is to done.

Everything ALTAI already had was one bounded step — scan, plan, hand out a task,
record the outcome — and a human ran the steps in a loop. This module *is* the
loop. A single call:

1. rescans and rebuilds the project model, code graph and task graph,
2. optionally writes the pre-code design plan,
3. promotes the scored recommendations into real work instead of only listing them,
4. hands each dependency-ready task to the host agent CLI (:mod:`altai.executor`),
5. re-runs the project's own declared checks itself and records the result as
   evidence via the normal `done` / `fail` path — never by writing state directly,
6. sweeps for work the change itself created, and repeats until the project is
   done, blocked, or the run's budget is spent.

Two things keep step 4 honest. The runner, not the agent, records outcomes: an
agent that says it finished but leaves `pytest` red gets a failed attempt, and
after the usual three attempts the task blocks itself. And the checks are the
project's own declared commands, so "verified" means the repository's gates
passed, not that a model reported success.

Autonomy is :mod:`altai.autonomy`'s decision, not this module's. At ``full`` —
the default — a task whose text trips a stop-and-ask category is approved
automatically and the approval is written to the run log and the task's
evidence; at ``guarded`` the same task ends the run with
:data:`EXIT_POLICY_HOLD`.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .autonomy import Autonomy
from .autopilot import classify_task_dict
from .checkpoint import Checkpointer
from .design import generate_design_plan
from .executor import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_CHECK_TIMEOUT,
    ExecutionPlan,
    build_prompt,
    detect_agent,
    project_checks,
    run_agent,
    run_checks,
    running_inside_agent,
)
from .graph import blocked_tasks, project_phase
from .intelligence.opportunity_finder import load_opportunities
from .intelligence.project_memory import record as record_memory
from .intelligence.project_model import load_model
from .memory import append_run_log, record_evidence
from .models import ProjectState
from .orchestrator import bootstrap, complete_task, fail_attempt, next_brief, promote_opportunity

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 3
EXIT_POLICY_HOLD = 5
#: Execution was requested but no host-agent CLI could be resolved.
EXIT_NO_AGENT = 6
#: The run hit its iteration or time budget with work still ready.
EXIT_INCOMPLETE = 7

#: How many tasks one `altai run` will attempt before handing control back.
DEFAULT_MAX_ITERATIONS = 25
#: Per-task ceiling on the agent's own turns, where its CLI supports one. An
#: unattended run's first line of defence against one stuck task consuming the
#: whole budget; 0 removes the ceiling.
DEFAULT_MAX_TURNS = 120
#: How many times an empty queue triggers a fresh scan before the run concludes.
#: A finished task often creates the next one (a new TODO, a new opportunity);
#: an unbounded sweep would let the project generate work forever.
DEFAULT_MAX_SWEEPS = 2
TOP_OPPORTUNITIES = 3

_INSTRUCTIONS = {
    "complete": (
        "Project complete: no ready work remains and every task is done or deliberately "
        "settled. Evidence is under .altai/evidence/, the audit trail under .altai/runs/log.md."
    ),
    "blocked": (
        "Nothing is ready and blocked tasks remain. Read the reasons below, fix the cause, "
        "then `altai unblock <id>` and re-run. A task blocked twice needs a decision, not "
        "another attempt."
    ),
    "policy_hold": (
        "Stopped: this task's own text matched a category that guarded autonomy holds for. "
        "Approve it and re-run with full autonomy (the default), or `altai skip` it."
    ),
    "no_agent": (
        "No host-agent CLI found, so nothing could be implemented. Install and sign in to "
        "`claude` or `codex`, or set ALTAI_AGENT_CMD, or use --plan-only to get the next "
        "task without executing it."
    ),
    "handoff": (
        "Plan-only: the task above was not implemented. Implement it yourself, then record "
        "the outcome with `altai done`/`fail`, or re-run without --plan-only."
    ),
    "incomplete": (
        "Budget spent with work still ready. Nothing was lost — re-run `altai run` to "
        "continue from exactly here."
    ),
}


@dataclass(slots=True)
class Iteration:
    """One task's trip through the loop."""

    task_id: str
    title: str
    #: done | failed | held | handoff
    outcome: str
    policy_flags: list[str] = field(default_factory=list)
    auto_approved: bool = False
    evidence: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    agent: dict[str, Any] | None = None
    reason: str = ""
    #: Commit this task produced, when checkpointing is on.
    commit: str = ""
    #: Whether a failed attempt's changes were discarded back to the checkpoint.
    rolled_back: bool = False
    cost_usd: float | None = None
    turns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunReport:
    phase: str
    autonomy: str
    iterations: list[Iteration] = field(default_factory=list)
    applied_recommendations: list[dict[str, Any]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    design: dict[str, str] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    instruction: str = ""
    duration: float = 0.0
    #: Sum of what the agent reported spending, when it reports anything.
    cost_usd: float | None = None
    exit_code: int = EXIT_OK

    @property
    def completed(self) -> list[Iteration]:
        return [item for item in self.iterations if item.outcome == "done"]

    @property
    def commits(self) -> list[str]:
        return [item.commit for item in self.iterations if item.commit]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["iterations"] = [item.to_dict() for item in self.iterations]
        data["completed"] = len(self.completed)
        data["commits"] = self.commits
        data.pop("exit_code")
        return data


def _apply_recommendations(
    root: Path,
    state: ProjectState,
    autonomy: Autonomy,
    seen: set[str],
) -> tuple[ProjectState, list[dict[str, Any]]]:
    """Promote every pending recommendation this autonomy level allows.

    Guarded autonomy keeps the historical behaviour of skipping a candidate whose
    own text trips a stop-and-ask category; full autonomy promotes it and records
    the approval.
    """
    applied: list[dict[str, Any]] = []
    for candidate in load_opportunities(root):
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        data = candidate.to_dict()
        flags = classify_task_dict(data)
        if flags and not autonomy.promotes_flagged:
            continue
        state, _ = promote_opportunity(root, candidate.id)
        # Recorded *and* acted on, in that order and in the same run: a
        # recommendation that only lands in memory is one nobody applies, and one
        # that is only applied leaves the next run no idea why the task exists.
        record_memory(
            root,
            "product-decisions",
            f"auto-applied recommendation ({candidate.kind}): {candidate.title}. "
            f"Promoted to a task in the same run under '{autonomy.level}' autonomy"
            + (f"; policy flags: {', '.join(flags)}" if flags else "")
            + ".",
            task_id=candidate.id,
        )
        if flags:
            note = autonomy.approval_note(candidate.id, flags)
            append_run_log(root, note)
            record_evidence(root, candidate.id, note)
        applied.append(data)
    return state, applied


def _failure_reason(agent_result, checks) -> str:
    if agent_result is not None and not agent_result.ok:
        detail = agent_result.output.splitlines()[-1] if agent_result.output else ""
        if agent_result.timed_out:
            return f"agent timed out after {agent_result.duration:.0f}s"
        return f"agent exited {agent_result.exit_code}: {detail}".strip()
    broken = [check for check in checks if not check.ok]
    if broken:
        first = broken[0]
        detail = first.output.splitlines()[-1] if first.output else ""
        return f"{first.label} check failed ({first.command}): {detail}".strip()
    return "unknown failure"


def run_project(
    root: Path,
    *,
    autonomy: Autonomy | None = None,
    plan_only: bool = False,
    design: bool | None = None,
    apply_recommendations: bool = True,
    rescan: bool = True,
    agent: str | None = None,
    checks: list[str] | None = None,
    commit: bool | None = None,
    rollback: bool | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_sweeps: int = DEFAULT_MAX_SWEEPS,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    check_timeout: float = DEFAULT_CHECK_TIMEOUT,
    time_budget: float | None = None,
    allow_nested: bool = False,
) -> RunReport:
    """Drive *root* to completion and report everything the run did."""
    root = Path(root).resolve()
    started = time.monotonic()
    autonomy = autonomy or Autonomy.from_env()
    notes: list[str] = []

    state = bootstrap(root, rescan=rescan)

    design_paths: dict[str, str] = {}
    if design is not False:
        # Default (``None``) is "attempt it": the design plan is deterministic,
        # cheap and inspectable, and a project whose model is not yet confirmed
        # just gets the note instead of the artifacts.
        try:
            design_paths = {name: str(path) for name, path in generate_design_plan(root).items()}
        except ValueError as error:
            # An unconfirmed project model is a reason to skip the design pass,
            # not to abandon a run that can still finish real work.
            notes.append(f"design pass skipped: {error}")

    agent_spec = None
    no_agent = False
    if not plan_only:
        if running_inside_agent() and not allow_nested and agent is None:
            plan_only = True
            notes.append(
                "running inside a host agent: handing the task to the caller instead of "
                "spawning a nested one (--allow-nested overrides)"
            )
        else:
            agent_spec = detect_agent(
                agent, bypass=autonomy.bypass_agent_approvals, max_turns=max_turns
            )
            if agent_spec is None:
                # Distinct from a requested plan-only run: the operator asked for
                # execution and did not get it, which a script must be able to see.
                plan_only = True
                no_agent = True
                notes.append("no host-agent CLI resolved")

    model = load_model(root)
    plan = ExecutionPlan(
        agent=agent_spec,
        checks=project_checks(model.commands if model else {}, checks),
        agent_timeout=agent_timeout,
        check_timeout=check_timeout,
    )

    checkpointer = (
        Checkpointer.start(root, commit=commit, rollback=rollback)
        if not plan_only
        else Checkpointer(root=root, reason="plan-only run makes no changes")
    )
    if checkpointer.reason:
        notes.append(f"checkpoints: {checkpointer.reason}")

    if not plan_only and not plan.checks:
        # Worth saying out loud: with no declared gates, "done" rests entirely on
        # the agent's own exit code, which is the weakest evidence this tool accepts.
        notes.append(
            "no verification commands declared in project-model.json; completion rests on the "
            "agent's exit code alone. Add a test/build command, or pass --check."
        )

    promoted_seen: set[str] = set()
    applied: list[dict[str, Any]] = []
    if apply_recommendations:
        state, applied = _apply_recommendations(root, state, autonomy, promoted_seen)

    iterations: list[Iteration] = []
    sweeps = 0
    exit_code: int | None = None

    while True:
        if time_budget is not None and time.monotonic() - started >= time_budget:
            exit_code = EXIT_INCOMPLETE
            break
        if len(iterations) >= max_iterations:
            exit_code = EXIT_INCOMPLETE
            break

        brief = next_brief(state)
        if brief is None:
            if rescan and sweeps < max_sweeps and project_phase(state.tasks) != "DONE":
                # A blocked project cannot be swept back to life, but a project
                # whose last change introduced new markers can.
                sweeps += 1
                state = bootstrap(root, rescan=True)
                if apply_recommendations:
                    state, more = _apply_recommendations(root, state, autonomy, promoted_seen)
                    applied.extend(more)
                if next_brief(state) is not None:
                    continue
            break

        task = brief["task"]
        flags = classify_task_dict(task)
        if autonomy.holds(flags):
            iterations.append(
                Iteration(
                    task_id=task["id"],
                    title=task["title"],
                    outcome="held",
                    policy_flags=flags,
                    reason="guarded autonomy holds stop-and-ask categories",
                )
            )
            exit_code = EXIT_POLICY_HOLD
            break
        if flags:
            note = autonomy.approval_note(task["id"], flags)
            append_run_log(root, note)
            record_evidence(root, task["id"], note)

        if plan_only:
            iterations.append(
                Iteration(
                    task_id=task["id"],
                    title=task["title"],
                    outcome="handoff",
                    policy_flags=flags,
                    auto_approved=bool(flags),
                )
            )
            break

        prompt = build_prompt(
            brief,
            root,
            autonomy_note=(
                "This run is unattended. Do not ask for confirmation; if a step is genuinely "
                "unsafe or undecidable, stop with a non-zero exit and say why."
                if autonomy.unattended
                else ""
            ),
        )
        agent_result = run_agent(plan.agent, prompt, root, timeout=plan.agent_timeout)
        check_results = run_checks(plan.checks, root, timeout=plan.check_timeout) if agent_result.ok else []
        evidence = [agent_result.evidence, *(check.evidence for check in check_results)]

        commit_sha = ""
        rolled_back = False
        if agent_result.ok and all(check.ok for check in check_results):
            state = complete_task(root, task["id"], evidence)
            outcome = "done"
            reason = ""
            # Commit after the state write, so a task recorded as done always has
            # its code in history rather than the other way round.
            commit_sha = checkpointer.commit_task(task["id"], task["title"], "\n".join(evidence))
        else:
            reason = _failure_reason(agent_result, check_results)
            state = fail_attempt(root, task["id"], reason)
            outcome = "failed"
            # A half-finished attempt left in the tree is what the next attempt
            # then has to understand before it can fix anything.
            rolled_back = checkpointer.rollback_task()
            if rolled_back:
                record_evidence(
                    root, task["id"], f"rolled back to {checkpointer.baseline[:12]} after: {reason}"
                )

        iterations.append(
            Iteration(
                task_id=task["id"],
                title=task["title"],
                outcome=outcome,
                policy_flags=flags,
                auto_approved=bool(flags),
                evidence=evidence,
                checks=[check.to_dict() for check in check_results],
                agent=agent_result.to_dict(),
                reason=reason,
                commit=commit_sha,
                rolled_back=rolled_back,
                cost_usd=agent_result.cost_usd,
                turns=agent_result.turns,
            )
        )

    phase = project_phase(state.tasks)
    blocked = [{"id": t.id, "reason": t.blocked_reason} for t in blocked_tasks(state.tasks)]
    handed_off = bool(iterations) and iterations[-1].outcome == "handoff"
    if exit_code is None:
        if no_agent and phase != "DONE":
            exit_code = EXIT_NO_AGENT
        elif handed_off:
            exit_code = EXIT_OK
        elif phase == "DONE":
            exit_code = EXIT_OK
        else:
            exit_code = EXIT_BLOCKED

    key = {
        EXIT_OK: "handoff" if handed_off else "complete",
        EXIT_BLOCKED: "blocked",
        EXIT_POLICY_HOLD: "policy_hold",
        EXIT_NO_AGENT: "no_agent",
        EXIT_INCOMPLETE: "incomplete",
    }[exit_code]

    costs = [item.cost_usd for item in iterations if item.cost_usd is not None]
    report = RunReport(
        phase=phase,
        autonomy=autonomy.level,
        iterations=iterations,
        applied_recommendations=applied,
        opportunities=[c.to_dict() for c in load_opportunities(root)[:TOP_OPPORTUNITIES]],
        blocked=blocked,
        design=design_paths,
        plan=plan.to_dict(),
        checkpoint={
            "enabled": checkpointer.enabled,
            "rollback": checkpointer.rollback,
            "baseline": checkpointer.baseline,
            "reason": checkpointer.reason,
        },
        notes=notes,
        instruction=_INSTRUCTIONS[key],
        duration=round(time.monotonic() - started, 1),
        cost_usd=round(sum(costs), 4) if costs else None,
        exit_code=exit_code,
    )
    append_run_log(
        root,
        f"run: {len(report.completed)}/{len(iterations)} tasks completed, phase={phase}, "
        f"autonomy={autonomy.level}, commits={len(report.commits)}, exit={exit_code}",
    )
    return report
