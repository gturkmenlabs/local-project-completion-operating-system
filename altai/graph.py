from __future__ import annotations

from .models import SATISFIED, SETTLED, TERMINAL, Task, TaskStatus

EXHAUSTED_REASON = "Attempt budget exhausted; a different approach or human input is required."
MISSING_DEP_REASON = "Depends on a task that does not exist: {dep}"
CYCLE_REASON = "Dependency cycle: {cycle}"


def find_cycles(tasks: list[Task]) -> list[list[str]]:
    """Return dependency cycles as lists of task IDs.

    Iterative DFS: a recursive implementation raises ``RecursionError`` on long
    dependency chains, which would surface as a traceback on every command.

    Without cycle detection a cyclic graph produces no ready task *and* no
    completion: a silent deadlock indistinguishable from "everything is blocked".
    """
    by_id = {task.id: task for task in tasks}
    color: dict[str, int] = {}  # 0 = unvisited, 1 = on stack, 2 = finished
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    for start in by_id:
        if color.get(start, 0) != 0:
            continue
        path: list[str] = []
        # Each frame is (node, iterator over its dependencies).
        stack: list[tuple[str, iter]] = [(start, iter(by_id[start].dependencies))]
        color[start] = 1
        path.append(start)

        while stack:
            node, deps = stack[-1]
            advanced = False
            for dep in deps:
                if dep not in by_id:
                    continue
                state = color.get(dep, 0)
                if state == 0:
                    color[dep] = 1
                    path.append(dep)
                    stack.append((dep, iter(by_id[dep].dependencies)))
                    advanced = True
                    break
                if state == 1:
                    cycle = path[path.index(dep) :] + [dep]
                    key = tuple(cycle)
                    if key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append(cycle)
            if not advanced:
                color[node] = 2
                path.pop()
                stack.pop()
    return cycles


def missing_dependencies(tasks: list[Task]) -> dict[str, list[str]]:
    known = {task.id for task in tasks}
    result: dict[str, list[str]] = {}
    for task in tasks:
        missing = [dep for dep in task.dependencies if dep not in known]
        if missing:
            result[task.id] = missing
    return result


def _auto_block(task: Task, reason: str, sink: list[Task]) -> None:
    if task.status == TaskStatus.BLOCKED and task.blocked_auto:
        return  # First matching rule wins, so the reason order below is meaningful.
    task.status_before_block = task.status_before_block or task.status.value
    task.status = TaskStatus.BLOCKED
    task.blocked_reason = reason
    task.blocked_auto = True
    sink.append(task)


def apply_blocks(tasks: list[Task]) -> list[Task]:
    """Promote implicit dead ends into explicit ``BLOCKED`` state.

    Auto blocks from a previous run are cleared first and recomputed, so a stale
    reason cannot survive after the underlying problem is fixed (e.g. a missing
    dependency that has since been created). Manual blocks are left alone —
    only ``unblock`` clears those. A settled task (DONE/SKIPPED) is never
    touched: inference must not revert a recorded outcome.
    """
    for task in tasks:
        if task.status == TaskStatus.BLOCKED and task.blocked_auto:
            restored = task.status_before_block or TaskStatus.UNKNOWN.value
            try:
                task.status = TaskStatus(restored)
            except ValueError:
                task.status = TaskStatus.UNKNOWN
            if task.status == TaskStatus.BLOCKED:  # defensive: never restore into a block
                task.status = TaskStatus.UNKNOWN
            task.blocked_reason = ""
            task.blocked_auto = False
            task.status_before_block = ""

    newly_blocked: list[Task] = []
    by_id = {task.id: task for task in tasks}

    # Ordered by how actionable the reason is: a broken graph explains more than
    # an exhausted budget, so it is reported first.
    for task_id, missing in missing_dependencies(tasks).items():
        task = by_id[task_id]
        if task.status in SETTLED or (task.status == TaskStatus.BLOCKED and not task.blocked_auto):
            continue
        _auto_block(task, MISSING_DEP_REASON.format(dep=", ".join(missing)), newly_blocked)

    for cycle in find_cycles(tasks):
        for task_id in cycle:
            task = by_id.get(task_id)
            if task is None or task.status in SETTLED:
                continue
            if task.status == TaskStatus.BLOCKED and not task.blocked_auto:
                continue
            _auto_block(task, CYCLE_REASON.format(cycle=" -> ".join(cycle)), newly_blocked)

    for task in tasks:
        if task.status in TERMINAL:
            continue
        if task.exhausted:
            _auto_block(task, EXHAUSTED_REASON, newly_blocked)

    return newly_blocked


def next_ready_task(tasks: list[Task]) -> Task | None:
    satisfied = {task.id for task in tasks if task.status in SATISFIED}
    candidates = [
        task
        for task in tasks
        if task.status not in TERMINAL
        and all(dep in satisfied for dep in task.dependencies)
        and not task.exhausted
    ]
    return min(
        candidates,
        key=lambda task: (len(task.dependencies), task.attempts, task.id),
        default=None,
    )


def blocked_tasks(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if task.status == TaskStatus.BLOCKED]


def project_complete(tasks: list[Task]) -> bool:
    return bool(tasks) and all(task.status in SATISFIED for task in tasks)


def project_phase(tasks: list[Task]) -> str:
    """One of ``DONE`` / ``BLOCKED`` / ``ACTIVE`` / ``EMPTY``."""
    if not tasks:
        return "EMPTY"
    if project_complete(tasks):
        return "DONE"
    if next_ready_task(tasks) is not None:
        return "ACTIVE"
    return "BLOCKED"
