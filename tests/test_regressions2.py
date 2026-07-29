"""Regressions for the defects found in the second adversarial review."""

import json
import os
import stat
import sys

import pytest

from altai.cli import EXIT_BLOCKED, EXIT_COMPLETE, EXIT_ERROR, main
from altai.graph import apply_blocks
from altai.memory import load_state, save_state, state_path
from altai.models import SCHEMA_VERSION, ProjectState, Task, TaskStatus
from altai.orchestrator import (
    add_task,
    block_task,
    bootstrap,
    complete_task,
    fail_attempt,
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


def _todo(project):
    return next(t.id for t in load_state(project).tasks if t.id.startswith("todo-"))


def _finish_all(project):
    complete_task(project, RESEARCH_ID, ["docs read"])
    # The bare `project` fixture has no README and no run command, so the gap
    # analyzer opens tasks for both. quality-gates now waits on the
    # purpose-confirmation gap specifically, so it must be settled first, not
    # merely out of the way.
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    complete_task(project, GATES_ID, ["pytest -> ok"])
    complete_task(project, _todo(project), ["pytest -> 1 passed"])
    return complete_task(project, FINAL_ID, ["build ok"])


def _write_legacy(tmp_path, tasks):
    path = state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"root": str(tmp_path), "name": "legacy", "tasks": tasks}),
        encoding="utf-8",
    )


# R2-1: stale evidence must not satisfy a later `done`
def test_every_completion_needs_fresh_evidence(project):
    complete_task(project, RESEARCH_ID, ["docs read"])
    state = load_state(project)
    state.task(RESEARCH_ID).status = TaskStatus.UNKNOWN
    save_state(state)
    with pytest.raises(ValueError, match="without evidence"):
        complete_task(project, RESEARCH_ID, [""])
    with pytest.raises(ValueError, match="without evidence"):
        complete_task(project, RESEARCH_ID, [])


def test_reopened_final_cannot_be_closed_on_old_evidence(project):
    _finish_all(project)
    add_task(project, "Late work", task_id="late")
    state = load_state(project)
    assert state.task(FINAL_ID).status != TaskStatus.DONE
    assert state.task(FINAL_ID).evidence == [], "evidence predating the change must be cleared"
    complete_task(project, "late", ["pytest -> ok"])
    with pytest.raises(ValueError, match="without evidence"):
        complete_task(project, FINAL_ID, [""])


# R2-2: `fail` must not be a back door around the `done` guards
def test_fail_refuses_blocked_task(project):
    block_task(project, RESEARCH_ID, "needs human sign-off")
    with pytest.raises(ValueError, match="blocked"):
        fail_attempt(project, RESEARCH_ID, "trying anyway")
    saved = load_state(project)
    assert saved.task(RESEARCH_ID).status == TaskStatus.BLOCKED
    assert saved.task(RESEARCH_ID).blocked_reason == "needs human sign-off"


def test_fail_refuses_settled_task(project):
    complete_task(project, RESEARCH_ID, ["docs read"])
    with pytest.raises(ValueError, match="already done"):
        fail_attempt(project, RESEARCH_ID, "x")
    assert load_state(project).task(RESEARCH_ID).status == TaskStatus.DONE


# R2-3: a rescan that finds new work must reopen final verification
def test_rescan_reopens_final_verification(project):
    _finish_all(project)
    assert load_state(project).task(FINAL_ID).status == TaskStatus.DONE

    (project / "new.py").write_text("# TODO: brand new critical work\n", encoding="utf-8")
    state = bootstrap(project)
    assert state.task(FINAL_ID).status != TaskStatus.DONE
    assert state.task(FINAL_ID).evidence == []


# R2-4: a refused unblock must not consume the budget
def test_failed_unblock_records_nothing(project):
    state, _ = add_task(project, "Needs ghost", task_id="needsghost")
    state.task("needsghost").dependencies.append("ghost")
    save_state(state)
    bootstrap(project, rescan=False)

    for _ in range(3):
        with pytest.raises(ValueError, match="cannot be unblocked"):
            unblock_task(project, "needsghost")
    assert load_state(project).task("needsghost").unblocks == 0

    add_task(project, "Ghost", task_id="ghost")
    saved = load_state(project)
    assert saved.task("needsghost").status != TaskStatus.BLOCKED
    block_task(project, "needsghost", "human gate")
    unblock_task(project, "needsghost")  # budget was never burned
    assert load_state(project).task("needsghost").unblocks == 1


def test_unblock_refuses_unblocked_task(project):
    with pytest.raises(ValueError, match="not blocked"):
        unblock_task(project, RESEARCH_ID)


# R2-5: no traceback may escape the CLI
def test_corrupt_task_shapes_produce_clean_errors(project, capsys):
    for payload in (
        {"tasks": "not a list"},
        {"tasks": [{"id": "a", "dependencies": "notalist"}]},
        {"tasks": [{"id": "a", "attempts": "many"}]},
        {"tasks": ["not an object"]},
    ):
        state_path(project).write_text(json.dumps(payload), encoding="utf-8")
        assert main(["status", "--path", str(project)]) == EXIT_ERROR
        assert "Hata:" in capsys.readouterr().err


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="Windows or root ignores permission bits",
)
def test_permission_error_is_reported_not_raised(project, capsys):
    workspace = project / ".altai"
    mode = workspace.stat().st_mode
    workspace.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert main(["add", "y", "--id", "yy", "--path", str(project)]) == EXIT_ERROR
        assert "Hata:" in capsys.readouterr().err
    finally:
        workspace.chmod(mode)


def test_argparse_error_returns_exit_code(capsys):
    assert main(["done"]) != 0
    capsys.readouterr()


# R2-6: Turkish titles must produce valid ids
@pytest.mark.parametrize(
    "title, expected",
    [
        ("Görev tamamlansın", "gorev-tamamlansin"),
        ("Şifre değiştirme", "sifre-degistirme"),
        ("ÜÇ ÖĞE", "uc-oge"),
        ("日本語", "task"),
    ],
)
def test_non_ascii_titles_slug_cleanly(project, title, expected):
    _, task = add_task(project, title)
    assert task.id == expected


# R2-7: migration must not duplicate completed legacy work
def test_migration_drops_all_legacy_tasks(tmp_path):
    (tmp_path / "app.py").write_text("# TODO: alpha thing\n", encoding="utf-8")
    _write_legacy(
        tmp_path,
        [
            {"id": "todo-1", "title": "alpha thing", "status": "done", "evidence": ["old"]},
            {"id": "todo-2", "title": "gone", "status": "blocked", "blocked_reason": "no creds"},
        ],
    )
    state = bootstrap(tmp_path)
    ids = [task.id for task in state.tasks]
    assert "todo-1" not in ids and "todo-2" not in ids
    assert len([i for i in ids if i.startswith("todo-")]) == 1
    assert any("dropped 2" in risk for risk in state.risks)
    assert any("no creds" in risk for risk in state.risks)


def test_mutating_a_legacy_project_demands_start_first(tmp_path):
    _write_legacy(tmp_path, [{"id": "todo-1", "title": "x", "status": "unknown"}])
    with pytest.raises(ValueError, match="older ALTAI state format"):
        complete_task(tmp_path, "todo-1", ["x"])
    # Nothing was destroyed by the refusal.
    assert json.loads(state_path(tmp_path).read_text())["tasks"][0]["id"] == "todo-1"


# R2-8: stale-lock recovery must not let a departing process delete a live lock
def test_lock_release_only_removes_its_own_lock(project):
    lock = project / ".altai" / ".state.lock"
    from altai.memory import state_lock

    with state_lock(project):
        assert lock.exists()
        lock.write_text("someone-else", encoding="ascii")
    assert lock.exists(), "a foreign lock must survive our release"
    lock.unlink()


# R2-9: an existing .gitignore must be upgraded
def test_existing_gitignore_is_upgraded(project):
    ignore = project / ".altai" / ".gitignore"
    ignore.write_text("runs/\nevidence/\n", encoding="utf-8")
    bootstrap(project)
    lines = ignore.read_text(encoding="utf-8").splitlines()
    assert "tool/" in lines
    assert lines.count("runs/") == 1


# R2-10: apply_blocks must never revert a settled task
def test_settled_task_is_never_auto_blocked():
    for status in (TaskStatus.DONE, TaskStatus.SKIPPED):
        task = Task(id="a", title="A", status=status, blocked_auto=True, dependencies=["ghost"])
        apply_blocks([task])
        assert task.status == status


def test_clearing_an_auto_block_restores_prior_status():
    task = Task(id="a", title="A", status=TaskStatus.TESTING, dependencies=["ghost"])
    tasks = [task]
    apply_blocks(tasks)
    assert task.status == TaskStatus.BLOCKED
    tasks.append(Task(id="ghost", title="G"))
    apply_blocks(tasks)
    assert task.status == TaskStatus.TESTING


def test_graph_reason_prefers_the_broken_edge_over_the_budget():
    task = Task(id="a", title="A", attempts=3, max_attempts=3, dependencies=["ghost"])
    apply_blocks([task])
    assert "does not exist" in task.blocked_reason


# R2-11: skip settles a task so completion stays reachable
def test_skip_makes_completion_reachable(project):
    complete_task(project, RESEARCH_ID, ["docs read"])
    # Settle gap tasks (quality-gates now waits on the purpose-confirmation
    # gap specifically) before completing quality-gates.
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    complete_task(project, GATES_ID, ["pytest -> ok"])
    block_task(project, _todo(project), "needs a vendor API key we do not have")
    skip_task(project, _todo(project), "out of scope for this release")
    state = complete_task(project, FINAL_ID, ["build ok"])
    assert state.task(FINAL_ID).status == TaskStatus.DONE
    assert "atlandi" in __import__("altai.orchestrator", fromlist=["x"]).status_text(state)


def test_skipped_task_satisfies_dependencies(project):
    skip_task(project, RESEARCH_ID, "not applicable")
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    complete_task(project, GATES_ID, ["pytest -> ok"])
    assert load_state(project).task(GATES_ID).status == TaskStatus.DONE


# R2-12: risks are visible and self-clearing
def test_scan_risk_is_printed_and_cleared(tmp_path, capsys):
    many = tmp_path / "many.py"
    many.write_text("\n".join(f"# TODO: item {i}" for i in range(80)), encoding="utf-8")
    main(["start", str(tmp_path)])
    assert "Risk:" in capsys.readouterr().out

    many.write_text("# TODO: only one left\n", encoding="utf-8")
    state = bootstrap(tmp_path)
    assert not any(risk.startswith("[scan]") for risk in state.risks)


# R2-13: next --json stays machine-readable on every path
def test_next_json_on_blocked_and_complete(project, capsys):
    # The purpose-confirmation gap task is independent of research-project, so
    # settle it first — otherwise the graph still has ready work and never
    # reaches EXIT_BLOCKED / phase "BLOCKED".
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this regression test")
    block_task(project, RESEARCH_ID, "stuck")
    assert main(["next", "--json", "--path", str(project)]) == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"] is None and payload["phase"] == "BLOCKED"
    assert payload["blocked"][0]["reason"] == "stuck"

    unblock_task(project, RESEARCH_ID)
    _finish_all(project)
    capsys.readouterr()
    assert main(["next", "--json", "--path", str(project)]) == EXIT_COMPLETE
    assert json.loads(capsys.readouterr().out)["phase"] == "DONE"


# R2-14: a typo'd path must not silently create a workspace
def test_missing_directory_is_an_error(tmp_path, capsys):
    ghost = tmp_path / "typo" / "deep"
    assert main(["start", str(ghost)]) == EXIT_ERROR
    assert not ghost.exists()


# R2-15: the state file must be readable by teammates, not 0600
@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_state_file_is_group_readable(project):
    mode = stat.S_IMODE(state_path(project).stat().st_mode)
    assert mode & stat.S_IRGRP or (0o644 & ~_current_umask()) == mode


def _current_umask() -> int:
    value = os.umask(0)
    os.umask(value)
    return value


def test_schema_version_is_current_after_start(project):
    assert json.loads(state_path(project).read_text())["schema_version"] == SCHEMA_VERSION


def test_state_roundtrip_preserves_new_fields(tmp_path):
    state = ProjectState(
        root=tmp_path,
        name="x",
        tasks=[Task(id="a", title="A", unblocks=2, status_before_block="coding")],
    )
    save_state(state)
    loaded = load_state(tmp_path)
    assert loaded.task("a").unblocks == 2
    assert loaded.task("a").status_before_block == "coding"
