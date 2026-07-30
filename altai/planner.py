from __future__ import annotations

from .intelligence.gap_analyzer import CONFIRM_MODEL_ID
from .models import SATISFIED, ProjectState, Task, TaskStatus
from .research import BENCHMARK_TASK_ID

RESEARCH_ID = "research-project"
#: One definition, two users: the planner creates this task and
#: :mod:`altai.research` keys its product-benchmark brief off the same id.
BENCHMARK_ID = BENCHMARK_TASK_ID
GATES_ID = "quality-gates"
FINAL_ID = "final-verification"
#: Scaffolding tasks ALTAI always injects. Order matters: research -> gates -> final.
STANDARD_IDS = (RESEARCH_ID, BENCHMARK_ID, GATES_ID, FINAL_ID)
#: Confirming what the project is *for* must happen before quality gates are
#: established, not after — gates are meaningless against an unconfirmed
#: purpose. Exempt from the forced GATES_ID dependency every other discovered
#: task gets.
PURPOSE_FIRST_IDS = (CONFIRM_MODEL_ID,)


def _standard_tasks() -> list[Task]:
    return [
        Task(
            id=RESEARCH_ID,
            title="Research implementation patterns",
            description="Use official documentation first, then inspect maintained reference projects.",
            acceptance=["Sources recorded", "Decisions summarized", "No blind copy-paste"],
        ),
        # The task graph can only ever close contradictions the repository
        # already declares; nothing in a repository says what comparable
        # finished products do better. This is the one scaffold task whose
        # answer has to come from outside, and it is worded so its output is
        # adopted work rather than a document nobody reads.
        Task(
            id=BENCHMARK_ID,
            title="Benchmark comparable finished products and adopt what is missing",
            description=(
                "Research current products that already do what this project's confirmed "
                "purpose describes. Identify what they do that this repository does not, "
                "decide adopt or reject for each, and turn every adopted finding into work "
                "in the same pass."
            ),
            dependencies=[RESEARCH_ID],
            acceptance=[
                "At least three current sources with access dates in .altai/research/"
                f"{BENCHMARK_ID}.md",
                "Every finding marked adopt or reject, with the reason",
                "Each adopted finding recorded via `altai learn product-decisions`",
                "Each adopted finding added as a task via `altai add` in the same pass",
                "Rejected findings are recorded too, so they are not re-researched",
            ],
        ),
        Task(
            id=GATES_ID,
            title="Establish quality gates",
            description="Identify build, test, lint, type-check and security checks.",
            dependencies=[RESEARCH_ID],
            acceptance=["Commands documented", "Commands executable", "Failure policy defined"],
        ),
        Task(
            id=FINAL_ID,
            title="Run final project verification",
            description="Run all quality gates and verify the primary user path.",
            dependencies=[GATES_ID],
            acceptance=["Build passes", "Tests pass", "Primary flow verified", "README updated"],
        ),
    ]


def enrich_plan(state: ProjectState) -> ProjectState:
    """Insert scaffolding tasks and wire dependencies.

    Idempotent: running it on an already-enriched state must not duplicate
    tasks or grow the dependency lists on every invocation.
    """
    existing = {task.id for task in state.tasks}
    missing = [task for task in _standard_tasks() if task.id not in existing]
    state.tasks = missing + state.tasks

    by_id = {task.id: task for task in state.tasks}

    for task in state.tasks:
        if task.id in STANDARD_IDS:
            continue
        # Nothing may depend on final-verification: it depends on everything, so
        # such an edge is a guaranteed cycle that no CLI command could clear.
        if FINAL_ID in task.dependencies:
            task.dependencies = [dep for dep in task.dependencies if dep != FINAL_ID]
            task.notes = (
                f"{task.notes} Dropped dependency on {FINAL_ID} (would create a cycle)."
            ).strip()
        if task.id in PURPOSE_FIRST_IDS:
            # Strip it even if a merge from an earlier run carried it forward.
            task.dependencies = [dep for dep in task.dependencies if dep != GATES_ID]
        elif GATES_ID not in task.dependencies:
            task.dependencies.insert(0, GATES_ID)

    final = by_id.get(FINAL_ID)
    if final is not None:
        # Final verification gates on everything else, deduplicated and ordered.
        final.dependencies = [task.id for task in state.tasks if task.id != FINAL_ID]

    gates = by_id.get(GATES_ID)
    if gates is not None:
        if RESEARCH_ID in by_id and RESEARCH_ID not in gates.dependencies:
            gates.dependencies.insert(0, RESEARCH_ID)
        # The inverse of the exemption above: removing GATES_ID from a
        # purpose-first task's dependencies only makes confirmation not
        # *wait on* gates. Gates must still wait on confirmation, or an
        # unconfirmed purpose does not actually block anything and the whole
        # point of PURPOSE_FIRST_IDS is just cosmetic ordering.
        for purpose_id in PURPOSE_FIRST_IDS:
            if purpose_id in by_id and purpose_id not in gates.dependencies:
                gates.dependencies.append(purpose_id)

    reconcile_final(state)
    return state


def reconcile_final(state: ProjectState) -> bool:
    """Reopen ``final-verification`` if unfinished work exists beneath it.

    Whether new work arrives via ``add``, a rescan, an unblock or a reverted
    task, a completed final verification no longer covers the project. Returns
    True when it was reopened.
    """
    final = state.task(FINAL_ID)
    if final is None or final.status not in SATISFIED:
        return False
    stale = [
        task.id
        for task in state.tasks
        if task.id != FINAL_ID and task.status not in SATISFIED
    ]
    if not stale:
        return False
    final.status = TaskStatus.UNKNOWN
    final.attempts = 0
    # Old evidence describes a project state that no longer exists.
    final.evidence = []
    final.notes = f"Reopened: unfinished work remains ({', '.join(stale[:5])})."
    return True
