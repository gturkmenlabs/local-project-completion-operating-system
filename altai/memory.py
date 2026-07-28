from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import SCHEMA_VERSION, ProjectState, Task, TaskStatus, validate_task_id
from .scanner import SCAN_RISK_PREFIX

WORKSPACE_DIRNAME = ".altai"
STATE_FILENAME = "project-state.json"
LOCK_FILENAME = ".state.lock"
#: Progress fields that a rescan must never overwrite.
PRESERVED_FIELDS = (
    "status",
    "attempts",
    "evidence",
    "blocked_reason",
    "blocked_auto",
    "notes",
    "unblocks",
)

GITIGNORE_LINES = ("tool/", "runs/", "evidence/", ".state.lock")

#: v0.1 numbered its scan-derived tasks positionally.
LEGACY_TODO_RE = re.compile(r"^todo-\d+$")
LOCK_TIMEOUT_SECONDS = 30.0
#: A lock older than this is assumed to belong to a crashed process. Generous,
#: because breaking a live lock is far worse than waiting for a dead one.
LOCK_STALE_SECONDS = 900.0


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def workspace_path(root: Path) -> Path:
    return Path(root).resolve() / WORKSPACE_DIRNAME


def state_path(root: Path) -> Path:
    return workspace_path(root) / STATE_FILENAME


def init_workspace(root: Path) -> Path:
    workspace = workspace_path(root)
    for folder in (workspace, workspace / "research", workspace / "runs", workspace / "evidence"):
        folder.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(workspace)
    return workspace


def _ensure_gitignore(workspace: Path) -> None:
    """Add any missing entries instead of only writing the file when absent.

    A workspace created by an older version keeps its stale contents forever
    otherwise, so the vendored tool ends up committed.
    """
    path = workspace / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if not missing:
        return
    body = "\n".join([*existing, *missing]).strip() + "\n"
    path.write_text(body, encoding="utf-8")


def _owner_alive(path: Path) -> bool:
    """True unless the PID recorded in the lock file is provably gone.

    Unknown or unreadable ownership is treated as alive: breaking a live lock is
    much worse than waiting for a dead one.
    """
    try:
        pid = int(path.read_text(encoding="ascii").split(":", 1)[0])
    except (OSError, ValueError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


@contextlib.contextmanager
def state_lock(root: Path):
    """Serialize read-modify-write cycles on the state file.

    Every mutation is load -> mutate -> save. Without a lock two agents working
    in parallel silently clobber each other: the second writer saves a state it
    loaded before the first writer's change, discarding completed work.
    """
    path = init_workspace(root) / LOCK_FILENAME
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    token = f"{os.getpid()}:{time.time_ns()}"
    handle = None
    while handle is None:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            expired = False
            try:
                age = time.time() - path.stat().st_mtime
                # A lock whose owner process is gone is dead now, not in 15
                # minutes. The age guard keeps PID reuse from biting.
                expired = age > LOCK_STALE_SECONDS or (age > 2.0 and not _owner_alive(path))
            except OSError:
                pass
            if expired:
                path.unlink(missing_ok=True)
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Another ALTAI process holds {path}. Delete it if no process is running."
                )
            if not expired:
                time.sleep(0.05)
    try:
        os.write(handle, token.encode("ascii"))
        os.close(handle)
        yield
    finally:
        # Only remove our own lock. If a stale-lock breaker handed the lock to
        # someone else, deleting it here would cascade the failure.
        try:
            if path.read_text(encoding="ascii") == token:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_text(path: Path, payload: str, prefix: str = ".altai-") -> Path:
    """Write via a temp file in the same directory, then rename.

    A crash mid-write must not destroy recorded progress, and a reader must
    never observe a half-written file.
    """
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
        # mkstemp creates 0600; these files are shared team data, not secrets.
        os.chmod(tmp_name, 0o644 & ~_umask())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def save_state(state: ProjectState) -> Path:
    """Atomic write: a crash mid-write must not destroy recorded progress."""
    init_workspace(state.root)
    state.schema_version = SCHEMA_VERSION
    path = state_path(state.root)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    return atomic_write_text(path, payload, prefix=".state-")


def load_state(root: Path) -> ProjectState | None:
    """Return the persisted state, or ``None`` when there is nothing usable."""
    path = state_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # Deliberately *not* migrated here: migration is destructive (it drops
    # legacy tasks that only a rescan can re-create), so only `start` may do it.
    return ProjectState.from_dict(data, root=Path(root).resolve())


def migrate(state: ProjectState) -> ProjectState:
    """Bring an older state file up to the current schema.

    v0.1 files predate the ``discovered`` flag, so every task in them would load
    as hand-added and therefore immortal: ``merge_state`` would keep the stale
    positional ``todo-1`` entries forever *alongside* the content-addressed tasks
    generated for the very same markers.
    """
    if state.schema_version >= SCHEMA_VERSION:
        return state

    # Positional IDs cannot be mapped onto content-addressed ones, so *all* of
    # them go. Keeping the DONE ones would leave a duplicate of every marker
    # that is still present in the source, permanently satisfying nothing.
    legacy = [task for task in state.tasks if LEGACY_TODO_RE.match(task.id)]
    state.tasks = [task for task in state.tasks if not LEGACY_TODO_RE.match(task.id)]
    for task in state.tasks:
        if task.id == "baseline-verification":
            task.discovered = True

    if legacy:
        finished = sum(1 for task in legacy if task.status == TaskStatus.DONE)
        blocked = [task for task in legacy if task.status == TaskStatus.BLOCKED]
        summary = (
            f"Migration dropped {len(legacy)} positionally-numbered task(s) from an older "
            f"state file ({finished} previously marked done); their TODO/FIXME markers are "
            "rescanned with stable IDs. Re-verify that work."
        )
        state.risks.append(summary)
        for task in blocked:
            state.risks.append(
                f"Previously blocked task '{task.title}' lost its block during migration: "
                f"{task.blocked_reason or 'no reason recorded'}"
            )
        with contextlib.suppress(OSError):
            append_run_log(state.root, f"migrate: dropped {len(legacy)} legacy task(s)")

    state.schema_version = SCHEMA_VERSION
    return state


def merge_state(previous: ProjectState | None, fresh: ProjectState) -> ProjectState:
    """Fold a fresh scan into recorded progress.

    Rules:
      * A task known to both keeps its recorded progress and takes the fresh
        description/acceptance (source lines move).
      * A task that only exists in the old state is kept if it is DONE (history)
        or was added by hand (``discovered is False``); otherwise the underlying
        marker is gone and so is the task.
      * Goals, stack and risks are re-derived but hand-written entries survive.
    """
    if previous is None:
        return fresh

    old_by_id = {task.id: task for task in previous.tasks}
    merged: list[Task] = []
    seen: set[str] = set()

    for task in fresh.tasks:
        old = old_by_id.get(task.id)
        if old is not None:
            for name in PRESERVED_FIELDS:
                setattr(task, name, getattr(old, name))
            task.max_attempts = old.max_attempts
            # Hand-written dependencies and acceptance criteria outrank defaults.
            if old.dependencies:
                task.dependencies = list(dict.fromkeys(old.dependencies + task.dependencies))
            if old.acceptance and not task.acceptance:
                task.acceptance = list(old.acceptance)
        merged.append(task)
        seen.add(task.id)

    for task in previous.tasks:
        if task.id in seen:
            continue
        if task.status == TaskStatus.DONE or not task.discovered:
            merged.append(task)

    fresh.tasks = merged
    fresh.goals = list(dict.fromkeys(previous.goals + fresh.goals))
    fresh.stack = list(dict.fromkeys(previous.stack + fresh.stack))
    # Scan-derived risks are re-derived every time; keeping the old ones would
    # leave a warning standing after the condition that caused it has cleared.
    kept = [risk for risk in previous.risks if not risk.startswith(SCAN_RISK_PREFIX)]
    fresh.risks = list(dict.fromkeys(kept + fresh.risks))
    return fresh


def record_evidence(root: Path, task_id: str, text: str) -> Path:
    """Append durable evidence for a task under ``.altai/evidence/``."""
    init_workspace(root)
    # The ID lands in a path; reject anything that could escape the workspace.
    validate_task_id(task_id)
    path = workspace_path(root) / "evidence" / f"{task_id}.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {stamp}\n\n{text.strip()}\n\n")
    return path


def append_run_log(root: Path, line: str) -> Path:
    init_workspace(root)
    path = workspace_path(root) / "runs" / "log.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {stamp} {line}\n")
    return path
