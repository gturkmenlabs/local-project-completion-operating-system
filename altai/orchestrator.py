from __future__ import annotations

import unicodedata
from pathlib import Path

from .graph import apply_blocks, blocked_tasks, next_ready_task, project_phase
from .intelligence.code_graph import build_code_graph, load_graph, related_files, save_graph
from .intelligence.gap_analyzer import gap_tasks
from .intelligence.opportunity_finder import find_opportunities, load_opportunities, save_opportunities
from .intelligence.project_memory import digest as memory_digest
from .intelligence.project_memory import has_content as memory_has_content
from .intelligence.project_memory import record as record_memory
from .intelligence.project_memory import record_rule as record_memory_rule
from .intelligence.project_model import (
    ProjectModelBuilder,
    load_model,
    merge_model,
    save_model,
)
from .memory import (
    append_run_log,
    init_workspace,
    load_state,
    merge_state,
    migrate,
    record_evidence,
    save_state,
    state_lock,
)
from .models import (
    MAX_UNBLOCKS,
    SATISFIED,
    SCHEMA_VERSION,
    SETTLED,
    ProjectState,
    Task,
    TaskStatus,
    validate_task_id,
)
from .planner import enrich_plan, reconcile_final
from .research import build_research_brief
from .scanner import SCAN_RISK_PREFIX, scan_project

AGENT_TASK_FILENAME = "AGENT_TASK.md"

AGENT_TASK_TEMPLATE = """# ALTAI Autonomous Completion Loop

Project: {name}
Stack: {stack}

## One command

`{command} run` does everything below in a single invocation: it applies the pending
recommendations, hands each dependency-ready task to a host agent, runs this project's own
checks, and records `done`/`fail` with evidence until the project is finished. Run from a
terminal it is unattended; run from inside Claude Code or Codex it hands the task to the
caller instead of spawning a nested agent, so it works as a per-iteration entry point.

## Loop

1. Run `{command} run` (or `{command} next`) to get the single active task and its research brief.
2. Research only what that task needs. Prefer official documentation. Save a short,
   source-backed note under `.altai/research/<task-id>.md`.
3. Write acceptance criteria before editing code.
4. Make the smallest complete change. Do not start unrelated features.
5. Run the relevant tests, lint, type checks, build, and a primary user-flow check.
6. On success: `{command} done <task-id> --evidence "<command> -> <result>"`.
7. On failure: state cause -> fix -> retest. Record the attempt with
   `{command} fail <task-id> --reason "<what broke>"`. After {max_attempts} attempts the task
   is blocked automatically; change strategy or ask the user.
8. Repeat from step 1 until `{command} status` reports `Durum: BITTI`.

## Rules

- Never overwrite `.altai/project-state.json` by hand; use the CLI so progress is preserved.
- Ask the user only for destructive actions, secrets, paid actions, publishing, or product
  decisions the repository cannot answer.
- Communicate in compressed mode: short sentences, no repetition, diffs over full files.

## Commands

```
{command} run                 # single command: plan, implement, verify, record, repeat
{command} run --safe          # same, but stop on destructive/credential/spending/publish
{command} status              # current state, no rescan
{command} start .             # rescan and merge new TODO/FIXME work into the plan
{command} next                # active task + research brief
{command} done <id> --evidence "pytest -> 42 passed"
{command} fail <id> --reason "import error in module X"
{command} block <id> --reason "needs production credentials"
{command} unblock <id>
{command} add "title" --depends-on <id> --acceptance "criterion"
{command} learn <category> "note"   # architecture|product-decisions|coding-conventions|
                                     # failed-approaches|user-preferences
{command} rule "condition" "rule"   # a check-before-acting rule, e.g. after a rejected approach
{command} opportunities             # scored, not-yet-adopted improvement candidates
{command} promote <opportunity-id>  # turn one into a real task
{command} autopilot                 # one rescan; next task + opportunities + policy check
                                     # (does not implement anything itself)
```
"""


def _command_hint(root: Path) -> str:
    """Prefer the vendored launcher when present, else the installed console script."""
    if (Path(root) / ".altai" / "tool" / "run.py").exists():
        return "python .altai/tool/run.py"
    return "altai"


def write_agent_task(state: ProjectState) -> Path:
    init_workspace(state.root)
    path = state.root / ".altai" / AGENT_TASK_FILENAME
    max_attempts = max((task.max_attempts for task in state.tasks), default=3)
    body = AGENT_TASK_TEMPLATE.format(
        name=state.name,
        stack=", ".join(state.stack) or "unknown",
        command=_command_hint(state.root),
        max_attempts=max_attempts,
    )
    if memory_has_content(state.root):
        body += (
            "\n## Project memory\n\n"
            "Read before starting: decisions, rejected approaches and conventions this "
            "project has already recorded.\n\n"
            f"{memory_digest(state.root)}\n"
        )
    path.write_text(body, encoding="utf-8")
    return path


def bootstrap(root: Path, rescan: bool = True) -> ProjectState:
    """Load persisted progress, optionally fold in a fresh scan, and save.

    This never discards recorded status, attempts or evidence.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"No such directory: {root}")
    # Scanning is read-only and can take a while on a large repo; holding the
    # lock across it invites the stale-lock breaker to steal it.
    fresh = scan_project(root) if rescan else None
    fresh_model = ProjectModelBuilder(root).build()
    # Purely derived, so it is rebuilt whole rather than merged like the model.
    fresh_graph = build_code_graph(root) if rescan else None
    with state_lock(root):
        previous = load_state(root)
        if previous is not None:
            previous = migrate(previous)
        merged_model = merge_model(load_model(root), fresh_model)
        if fresh is not None or previous is None:
            base = fresh if fresh is not None else scan_project(root)
            # Gaps are re-derived from the merged model, the same way scan_project
            # re-derives TODO tasks, so a closed gap disappears on its own instead
            # of needing to be marked done by hand.
            base.tasks.extend(gap_tasks(merged_model))
            if fresh_graph is not None and fresh_graph.truncated:
                # Same [scan]-prefixed convention scan_project uses for its own
                # cap: merge_state drops it automatically once a rescan is no
                # longer truncated, so it never lingers past the condition
                # that caused it.
                base.risks.append(
                    f"{SCAN_RISK_PREFIX} code graph scan was truncated; more source files exist "
                    "than were analyzed, so related_files/opportunities may be incomplete. "
                    "Reduce vendored/generated bulk under the project root and re-run `start`."
                )
            state = merge_state(previous, base)
        else:
            state = previous
        state = enrich_plan(state)
        apply_blocks(state.tasks)
        save_state(state)
        save_model(merged_model)
        if fresh_graph is not None:
            save_graph(fresh_graph)
            # Advisory only — never auto-injected into state.tasks (see
            # opportunity_finder's module docstring for why). A candidate whose
            # ID already exists as a task (typically: already promoted) is not
            # suggested again.
            existing_ids = {task.id for task in state.tasks}
            save_opportunities(root, find_opportunities(merged_model, fresh_graph, existing_ids))
        write_agent_task(state)
    return state


def load_or_fail(root: Path) -> ProjectState:
    root = Path(root).resolve()
    state = load_state(root)
    if state is None:
        raise FileNotFoundError(
            "No .altai/project-state.json found. Run `altai start .` in the project first."
        )
    if state.schema_version < SCHEMA_VERSION:
        # Migration drops legacy tasks that only a rescan can re-create, so it
        # must not happen as a side effect of an unrelated mutation.
        raise ValueError(
            "This project uses an older ALTAI state format. Run `altai start .` once to "
            "migrate it before recording progress."
        )
    return enrich_plan(state)


def _persist(state: ProjectState, log_line: str) -> ProjectState:
    apply_blocks(state.tasks)
    if reconcile_final(state):
        append_run_log(state.root, "final-verification reopened: unfinished work remains")
    save_state(state)
    append_run_log(state.root, log_line)
    return state


def _require_task(state: ProjectState, task_id: str) -> Task:
    task = state.task(task_id)
    if task is None:
        known = ", ".join(t.id for t in state.tasks[:10])
        raise ValueError(f"Unknown task id '{task_id}'. Known ids include: {known}")
    return task


def _clean_evidence(evidence: list[str] | None) -> list[str]:
    """Blank strings are not evidence; ``-e ""`` must not satisfy the contract."""
    return [item.strip() for item in (evidence or []) if item and item.strip()]


def complete_task(root: Path, task_id: str, evidence: list[str] | None = None) -> ProjectState:
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        task = _require_task(state, task_id)
        if task.status == TaskStatus.BLOCKED:
            raise ValueError(
                f"Cannot complete blocked task '{task_id}' ({task.blocked_reason or 'no reason'}). "
                f"Resolve the block first: altai unblock {task_id}"
            )
        unmet = [
            dep
            for dep in task.dependencies
            if (dependency := state.task(dep)) is None or dependency.status not in SATISFIED
        ]
        if unmet:
            raise ValueError(f"Cannot complete '{task_id}': unmet dependencies {unmet}")

        # Fresh evidence every time. Accepting previously recorded evidence would
        # let a reopened task be re-closed with `-e ""` on the strength of a
        # verification that predates the change.
        clean = _clean_evidence(evidence)
        if not clean:
            raise ValueError(
                f"Cannot complete '{task_id}' without evidence. "
                'Pass --evidence "<command> -> <result>".'
            )
        for item in clean:
            task.evidence.append(item)
            record_evidence(state.root, task_id, item)

        task.status = TaskStatus.DONE
        task.blocked_reason = ""
        task.blocked_auto = False
        return _persist(state, f"done {task_id}")


def fail_attempt(root: Path, task_id: str, reason: str = "") -> ProjectState:
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        task = _require_task(state, task_id)
        # `fail` must not be a back door around the guards on `done`: it used to
        # clear a manual block and revert a settled task by writing CODING.
        if task.status == TaskStatus.BLOCKED:
            raise ValueError(
                f"'{task_id}' is blocked ({task.blocked_reason or 'no reason'}). "
                f"Clear it first: altai unblock {task_id}"
            )
        if task.status in SETTLED:
            raise ValueError(
                f"'{task_id}' is already {task.status.value}. Reopen it deliberately "
                "rather than recording a failed attempt against finished work."
            )
        task.attempts += 1
        # Leave the blocking decision to apply_blocks so the reason is always recorded.
        task.status = TaskStatus.CODING
        if reason:
            task.notes = f"attempt {task.attempts}: {reason}"
            record_evidence(state.root, task_id, f"attempt {task.attempts} failed: {reason}")
        return _persist(state, f"fail {task_id} ({task.attempts}/{task.max_attempts})")


def block_task(root: Path, task_id: str, reason: str) -> ProjectState:
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        task = _require_task(state, task_id)
        task.status = TaskStatus.BLOCKED
        task.blocked_reason = reason
        task.blocked_auto = False  # Manual blocks are sticky until explicitly unblocked.
        return _persist(state, f"block {task_id}: {reason}")


def unblock_task(root: Path, task_id: str) -> ProjectState:
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        task = _require_task(state, task_id)
        if task.status != TaskStatus.BLOCKED:
            raise ValueError(f"'{task_id}' is not blocked (status: {task.status.value}).")
        if task.unblocks >= MAX_UNBLOCKS:
            raise ValueError(
                f"'{task_id}' has already been unblocked {task.unblocks} times. "
                "Stop retrying and escalate to the user: the approach, not the attempt "
                f"count, is the problem. Use `altai skip {task_id} --reason ...` to settle it."
            )

        task.status = TaskStatus.UNKNOWN
        task.blocked_reason = ""
        task.blocked_auto = False
        task.status_before_block = ""
        task.attempts = 0

        # Dry-run the graph before committing: an unblock that the graph will
        # immediately undo must not consume the unblock budget.
        apply_blocks(state.tasks)
        if task.status == TaskStatus.BLOCKED:
            raise ValueError(
                f"'{task_id}' cannot be unblocked yet: {task.blocked_reason} "
                "Fix the dependency graph first. Nothing was recorded."
            )

        task.unblocks += 1
        return _persist(state, f"unblock {task_id} ({task.unblocks}/{MAX_UNBLOCKS})")


def skip_task(root: Path, task_id: str, reason: str) -> ProjectState:
    """Settle a task as deliberately not done.

    Without this, one permanently blocked task makes completion unreachable and
    the only escape is to fake evidence.
    """
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        task = _require_task(state, task_id)
        if task.status == TaskStatus.DONE:
            raise ValueError(f"'{task_id}' is already done.")
        task.status = TaskStatus.SKIPPED
        task.blocked_reason = ""
        task.blocked_auto = False
        task.notes = f"skipped: {reason}"
        record_evidence(state.root, task_id, f"skipped by decision: {reason}")
        return _persist(state, f"skip {task_id}: {reason}")


def learn(root: Path, category: str, note: str, task_id: str = "") -> Path:
    """Record one entry in `.altai/memory/<category>.md`."""
    return record_memory(Path(root).resolve(), category, note, task_id=task_id)


def add_rule(root: Path, condition: str, rule: str, task_id: str = "") -> Path:
    """Record one `{condition, rule}` entry in `.altai/memory/learned-rules.json`."""
    return record_memory_rule(Path(root).resolve(), condition, rule, task_id=task_id)


def _add_task_locked(
    state: ProjectState,
    title: str,
    task_id: str | None,
    depends_on: list[str] | None,
    acceptance: list[str] | None,
    description: str,
) -> tuple[ProjectState, Task]:
    """Core of :func:`add_task`, assuming the caller already holds ``state_lock``.

    Split out so :func:`promote_opportunity` can read the opportunity list,
    add the task, and rewrite the opportunity list as one atomic unit under a
    single lock acquisition — ``state_lock`` is not reentrant, so calling the
    public :func:`add_task` from inside another locked block would deadlock.
    """
    new_id = validate_task_id(task_id or _slug(title, {t.id for t in state.tasks}))
    if state.task(new_id) is not None:
        raise ValueError(f"Task '{new_id}' already exists")
    for dep in depends_on or []:
        if state.task(dep) is None:
            raise ValueError(f"Cannot depend on unknown task '{dep}'")
    task = Task(
        id=new_id,
        title=title,
        description=description,
        dependencies=list(depends_on or []),
        acceptance=list(acceptance or []),
        discovered=False,
    )
    state.tasks.append(task)
    state = enrich_plan(state)
    # reconcile_final in _persist reopens final-verification if needed.
    _persist(state, f"add {new_id}")
    return state, task


def add_task(
    root: Path,
    title: str,
    task_id: str | None = None,
    depends_on: list[str] | None = None,
    acceptance: list[str] | None = None,
    description: str = "",
) -> tuple[ProjectState, Task]:
    root = Path(root).resolve()
    with state_lock(root):
        state = load_or_fail(root)
        return _add_task_locked(state, title, task_id, depends_on, acceptance, description)


def promote_opportunity(root: Path, opportunity_id: str) -> tuple[ProjectState, Task]:
    """Turn one scored candidate from `.altai/opportunities.json` into real work.

    This is the only path from `opportunity_finder`'s output into the task
    graph — never automatic (see the module docstring for why). The resulting
    task is created with ``discovered=False`` so, like anything added by hand,
    it survives a rescan even though nothing re-emits it; the opportunity is
    removed from the pending list so it is not offered for promotion twice,
    and its own ID becomes the task ID so a later scan's ``exclude_ids`` still
    recognises it and does not re-suggest it as a fresh candidate.

    Reading the candidate list, adding the task and rewriting the candidate
    list all happen under one ``state_lock`` acquisition. That makes two
    *concurrent* promotions safe — the second cannot read a list the first
    has not yet written back. It does not make a single promotion atomic
    against a crash or a full disk *between* the two writes: the task can end
    up persisted while ``opportunities.json`` still lists it as pending. A
    retry after that must not raise "already exists" and leave the state
    stuck; it recovers by finishing the second half — removing the candidate
    — and returning the task that already exists instead of erroring.
    """
    root = Path(root).resolve()
    with state_lock(root):
        candidates = load_opportunities(root)
        candidate = next((c for c in candidates if c.id == opportunity_id), None)
        state = load_or_fail(root)

        existing = state.task(opportunity_id)
        if existing is not None:
            if candidate is not None:
                save_opportunities(root, [c for c in candidates if c.id != opportunity_id])
            return state, existing

        if candidate is None:
            known = ", ".join(c.id for c in candidates[:10])
            raise ValueError(
                f"Unknown opportunity id '{opportunity_id}'. Known ids include: {known}"
            )
        state, task = _add_task_locked(
            state,
            title=candidate.title,
            task_id=candidate.id,
            depends_on=None,
            acceptance=candidate.acceptance,
            description=candidate.description,
        )
        save_opportunities(root, [c for c in candidates if c.id != opportunity_id])
        return state, task


#: Turkish characters have no ASCII decomposition, so map them explicitly.
_TRANSLITERATE = str.maketrans(
    {
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "ß": "ss", "æ": "ae", "ø": "o", "å": "a", "ł": "l", "đ": "d",
    }
)


def _slug(title: str, taken: set[str]) -> str:
    """ASCII slug. ``str.isalnum()`` is True for 'ö' and 'ş', so a naive slug
    produces IDs that the ID validator then rejects — a first-contact failure in
    a tool whose entire interface is Turkish."""
    folded = unicodedata.normalize("NFKD", title.translate(_TRANSLITERATE).lower())
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    base = "".join(char if char.isalnum() else "-" for char in ascii_only)
    base = "-".join(part for part in base.split("-") if part)[:40].strip("-") or "task"
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def next_brief(state: ProjectState) -> dict | None:
    task = next_ready_task(state.tasks)
    if task is None:
        return None
    brief = build_research_brief(state.root, task.title, state.stack, task.id)
    result = {"task": task.to_dict(), "research": brief.to_dict()}
    graph = load_graph(state.root)
    if graph is not None:
        result["related_files"] = related_files(graph, f"{task.title} {task.description}")
    if memory_has_content(state.root):
        result["memory"] = memory_digest(state.root)
    return result


PHASE_LABELS = {
    "DONE": "BITTI",
    "ACTIVE": "DEVAM",
    "BLOCKED": "BLOKLU",
    "EMPTY": "BOS",
}


def status_text(state: ProjectState) -> str:
    done = sum(task.status == TaskStatus.DONE for task in state.tasks)
    skipped = sum(task.status == TaskStatus.SKIPPED for task in state.tasks)
    blocked = blocked_tasks(state.tasks)
    task = next_ready_task(state.tasks)
    phase = project_phase(state.tasks)
    counts = f"{done}/{len(state.tasks)} bitti."
    if skipped:
        counts += f" {skipped} atlandi."
    lines = [
        f"Proje: {state.name}",
        f"Stack: {', '.join(state.stack) or 'belirsiz'}",
        f"Is: {counts} {len(blocked)} bloklu.",
        f"Sonraki: {task.title if task else 'yok'}",
    ]
    if task:
        brief = build_research_brief(state.root, task.title, state.stack, task.id)
        lines.append(f"Ara: {brief.queries[0]}")
    for item in blocked[:3]:
        lines.append(f"Blok: {item.id} - {item.blocked_reason or 'sebep kayitli degil'}")
    # Risks used to be visible only in --json, so a truncated scan looked like a
    # clean one.
    for risk in state.risks[:3]:
        lines.append(f"Risk: {risk}")
    lines.append(f"Durum: {PHASE_LABELS[phase]}")
    return "\n".join(lines)
