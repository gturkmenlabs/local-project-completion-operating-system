import json

from altai.memory import load_state, merge_state, record_evidence, save_state, state_path
from altai.models import ProjectState, Task, TaskStatus
from altai.scanner import scan_project


def _project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    (tmp_path / "app.py").write_text("# TODO: add login\n# FIXME: fix parser\n", encoding="utf-8")
    return tmp_path


def test_save_then_load_roundtrip(tmp_path):
    state = scan_project(_project(tmp_path))
    state.tasks[0].status = TaskStatus.DONE
    state.tasks[0].evidence = ["pytest -> 3 passed"]
    save_state(state)

    loaded = load_state(tmp_path)
    assert loaded is not None
    assert loaded.tasks[0].status == TaskStatus.DONE
    assert loaded.tasks[0].evidence == ["pytest -> 3 passed"]


def test_rescan_does_not_wipe_progress(tmp_path):
    root = _project(tmp_path)
    first = scan_project(root)
    target = first.tasks[0]
    target.status = TaskStatus.DONE
    target.attempts = 2
    target.evidence = ["pytest -> ok"]
    save_state(first)

    merged = merge_state(load_state(root), scan_project(root))
    kept = merged.task(target.id)
    assert kept is not None
    assert kept.status == TaskStatus.DONE
    assert kept.attempts == 2
    assert kept.evidence == ["pytest -> ok"]


def test_merge_drops_vanished_markers_but_keeps_done_history(tmp_path):
    root = _project(tmp_path)
    first = scan_project(root)
    done, pending = first.tasks[0], first.tasks[1]
    done.status = TaskStatus.DONE
    save_state(first)

    (root / "app.py").write_text("# clean\n", encoding="utf-8")
    merged = merge_state(load_state(root), scan_project(root))
    ids = {task.id for task in merged.tasks}
    assert done.id in ids, "completed work should remain as history"
    assert pending.id not in ids, "an unfinished marker that disappeared should drop out"


def test_manual_tasks_survive_rescan(tmp_path):
    root = _project(tmp_path)
    state = scan_project(root)
    state.tasks.append(Task(id="manual-1", title="Manual work", discovered=False))
    save_state(state)

    merged = merge_state(load_state(root), scan_project(root))
    assert merged.task("manual-1") is not None


def test_save_is_atomic_and_valid_json(tmp_path):
    state = ProjectState(root=tmp_path, name="x", tasks=[Task(id="a", title="A")])
    save_state(state)
    data = json.loads(state_path(tmp_path).read_text(encoding="utf-8"))
    assert data["tasks"][0]["id"] == "a"
    assert not list(state_path(tmp_path).parent.glob(".state-*.tmp"))


def test_corrupt_state_returns_none_instead_of_raising(tmp_path):
    state = ProjectState(root=tmp_path, name="x")
    save_state(state)
    state_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert load_state(tmp_path) is None


def test_record_evidence_appends(tmp_path):
    path = record_evidence(tmp_path, "task-1", "first")
    record_evidence(tmp_path, "task-1", "second")
    content = path.read_text(encoding="utf-8")
    assert "first" in content and "second" in content
