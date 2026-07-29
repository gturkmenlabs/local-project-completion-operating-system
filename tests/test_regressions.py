"""Regressions for the defects found in the adversarial review of v0.2.

Each test is named after the behaviour it locks down, and fails against the
pre-fix code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from altai.cli import EXIT_BLOCKED, EXIT_COMPLETE, EXIT_OK, main
from altai.graph import find_cycles
from altai.memory import load_state, save_state, state_path
from altai.models import MAX_UNBLOCKS, SCHEMA_VERSION, ProjectState, Task, TaskStatus
from altai.orchestrator import (
    add_task,
    block_task,
    bootstrap,
    complete_task,
    skip_task,
    unblock_task,
)
from altai.planner import FINAL_ID, GATES_ID, RESEARCH_ID


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    (tmp_path / "app.py").write_text("# TODO: add login\n", encoding="utf-8")
    bootstrap(tmp_path)
    return tmp_path


def _finish_prereqs(project):
    complete_task(project, RESEARCH_ID, ["docs read"])
    # The bare `project` fixture has no README and no run command, so the gap
    # analyzer opens tasks for both. quality-gates now waits on the
    # purpose-confirmation gap specifically, so it must be settled first, not
    # merely out of the way.
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    complete_task(project, GATES_ID, ["pytest -> ok"])


# NEW-1
def test_depending_on_final_verification_cannot_deadlock(project):
    state, _ = add_task(project, "Evil", task_id="evil", depends_on=[FINAL_ID])
    assert FINAL_ID not in state.task("evil").dependencies
    assert find_cycles(state.tasks) == []
    assert state.task("evil").status != TaskStatus.BLOCKED


# NEW-2
def test_v01_state_file_migrates_without_duplicating_tasks(tmp_path):
    (tmp_path / "app.py").write_text("# TODO: add login\n# FIXME: fix parser\n", encoding="utf-8")
    legacy = {
        "root": str(tmp_path),
        "name": "legacy",
        "stack": ["Python"],
        "goals": [],
        "risks": [],
        "tasks": [
            {"id": "todo-1", "title": "add login", "status": "unknown", "dependencies": []},
            {"id": "todo-2", "title": "fix parser", "status": "unknown", "dependencies": []},
        ],
    }
    path = state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")

    state = bootstrap(tmp_path)
    ids = [task.id for task in state.tasks]
    assert "todo-1" not in ids and "todo-2" not in ids
    assert len([i for i in ids if i.startswith("todo-")]) == 2
    assert state.schema_version == SCHEMA_VERSION
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION


# NEW-3
def test_empty_evidence_is_rejected(project):
    for bogus in ([""], ["   "], []):
        with pytest.raises(ValueError, match="without evidence"):
            complete_task(project, RESEARCH_ID, bogus)
    assert load_state(project).task(RESEARCH_ID).status != TaskStatus.DONE


# NEW-4
def test_concurrent_writes_do_not_lose_progress(project):
    """Several agents mutating at once must not clobber each other's work."""
    targets = [f"work-{i}" for i in range(8)]
    for task_id in targets:
        add_task(project, task_id, task_id=task_id)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "altai.cli", "fail", target, "-r", f"note {target}",
             "--path", str(project)],
            env=env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for target in targets
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0

    saved = load_state(project)
    # Every writer's increment must survive; a lost update shows up as a zero.
    for target in targets:
        assert saved.task(target).attempts == 1, f"{target} lost its update"
        assert saved.task(target).notes.endswith(f"note {target}")


# NEW-5
def test_unblock_is_capped(project):
    for _ in range(MAX_UNBLOCKS):
        block_task(project, RESEARCH_ID, "stuck")
        unblock_task(project, RESEARCH_ID)
    block_task(project, RESEARCH_ID, "stuck again")
    with pytest.raises(ValueError, match="escalate"):
        unblock_task(project, RESEARCH_ID)


# NEW-6
def test_stale_auto_block_reason_is_recomputed(project):
    state, _ = add_task(project, "A", task_id="cyca")
    state.task("cyca").dependencies.append("cycb")
    save_state(state)

    state = bootstrap(project, rescan=False)
    assert "does not exist" in state.task("cyca").blocked_reason

    state, _ = add_task(project, "B", task_id="cycb")
    assert state.task("cyca").status != TaskStatus.BLOCKED
    assert state.task("cyca").blocked_reason == ""


def test_manual_block_is_not_cleared_by_recompute(project):
    block_task(project, RESEARCH_ID, "needs production credentials")
    state = bootstrap(project, rescan=True)
    assert state.task(RESEARCH_ID).status == TaskStatus.BLOCKED
    assert state.task(RESEARCH_ID).blocked_reason == "needs production credentials"


# NEW-7
def test_adding_work_reopens_final_verification(project):
    _finish_prereqs(project)
    todo = next(t for t in load_state(project).tasks if t.id.startswith("todo-"))
    complete_task(project, todo.id, ["pytest -> ok"])
    state = complete_task(project, FINAL_ID, ["build ok"])
    assert state.task(FINAL_ID).status == TaskStatus.DONE

    state, _ = add_task(project, "Late work", task_id="late")
    assert state.task(FINAL_ID).status != TaskStatus.DONE
    assert "late" in state.task(FINAL_ID).dependencies


# NEW-8
def test_blocked_task_cannot_be_completed(project):
    block_task(project, RESEARCH_ID, "needs credentials")
    with pytest.raises(ValueError, match="blocked"):
        complete_task(project, RESEARCH_ID, ["did it anyway"])
    assert load_state(project).task(RESEARCH_ID).status == TaskStatus.BLOCKED


# NEW-9
def test_path_traversal_task_id_is_rejected(project):
    with pytest.raises(ValueError, match="Invalid task id"):
        add_task(project, "pwn", task_id="../../../../tmp/PWNED")
    assert not (project.parent / "PWNED.md").exists()


def test_unknown_dependency_is_rejected_at_add_time(project):
    with pytest.raises(ValueError, match="unknown task"):
        add_task(project, "x", task_id="x", depends_on=["ghost"])


# NEW-10
def test_deep_dependency_chain_does_not_recurse(tmp_path):
    tasks = [Task(id="t0", title="t0")]
    tasks += [Task(id=f"t{i}", title=f"t{i}", dependencies=[f"t{i-1}"]) for i in range(1, 3000)]
    assert find_cycles(tasks) == []
    tasks[0].dependencies = ["t2999"]
    assert find_cycles(tasks)


# NEW-11
def test_vendored_tool_is_gitignored(project):
    ignore = (project / ".altai" / ".gitignore").read_text(encoding="utf-8")
    assert "tool/" in ignore


# NEW-12
def test_marker_cap_is_reported_as_a_risk(tmp_path):
    lines = "\n".join(f"# TODO: item {i}" for i in range(80))
    (tmp_path / "many.py").write_text(lines, encoding="utf-8")
    state = bootstrap(tmp_path)
    assert any("cap" in risk for risk in state.risks)


# NEW-13
def test_next_exit_codes_distinguish_blocked_from_complete(project, capsys):
    assert main(["next", "--path", str(project)]) == EXIT_OK

    # The purpose-confirmation gap task is independent of research-project, so
    # settle it first — otherwise the graph still has ready work and never
    # reaches EXIT_BLOCKED.
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    block_task(project, RESEARCH_ID, "stuck")
    assert main(["next", "--path", str(project)]) == EXIT_BLOCKED

    unblock_task(project, RESEARCH_ID)
    _finish_prereqs(project)
    todo = next(t for t in load_state(project).tasks if t.id.startswith("todo-"))
    complete_task(project, todo.id, ["ok"])
    complete_task(project, FINAL_ID, ["ok"])
    capsys.readouterr()
    assert main(["next", "--path", str(project)]) == EXIT_COMPLETE


def test_state_lock_is_released_on_error(project):
    with pytest.raises(ValueError):
        complete_task(project, "nope", ["x"])
    # A second call must not time out on a leaked lock.
    complete_task(project, RESEARCH_ID, ["docs read"])
    assert not (project / ".altai" / ".state.lock").exists()


def test_evidence_survives_a_failed_completion(project):
    with pytest.raises(ValueError):
        complete_task(project, FINAL_ID, ["premature"])
    saved = load_state(project)
    assert saved.task(FINAL_ID).status != TaskStatus.DONE


def test_schema_version_written_on_plain_save(tmp_path):
    state = ProjectState(root=tmp_path, name="x", tasks=[Task(id="a", title="A")])
    state.schema_version = 1
    save_state(state)
    assert json.loads(state_path(tmp_path).read_text(encoding="utf-8"))["schema_version"] == (
        SCHEMA_VERSION
    )
