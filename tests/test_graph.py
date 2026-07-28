from altai.graph import (
    apply_blocks,
    find_cycles,
    missing_dependencies,
    next_ready_task,
    project_complete,
    project_phase,
)
from altai.models import Task, TaskStatus


def test_dependency_order():
    first = Task(id="a", title="A")
    second = Task(id="b", title="B", dependencies=["a"])
    assert next_ready_task([second, first]).id == "a"
    first.status = TaskStatus.DONE
    assert next_ready_task([second, first]).id == "b"


def test_complete():
    assert project_complete([Task(id="a", title="A", status=TaskStatus.DONE)])


def test_exhausted_task_is_blocked_not_silently_skipped():
    task = Task(id="a", title="A", attempts=3, max_attempts=3)
    assert next_ready_task([task]) is None
    apply_blocks([task])
    assert task.status == TaskStatus.BLOCKED
    assert "Attempt budget" in task.blocked_reason
    assert project_phase([task]) == "BLOCKED"


def test_cycle_is_detected_and_reported():
    a = Task(id="a", title="A", dependencies=["b"])
    b = Task(id="b", title="B", dependencies=["a"])
    cycles = find_cycles([a, b])
    assert cycles, "expected a cycle to be reported"
    apply_blocks([a, b])
    assert a.status == TaskStatus.BLOCKED
    assert "Dependency cycle" in a.blocked_reason


def test_missing_dependency_blocks_task():
    task = Task(id="a", title="A", dependencies=["ghost"])
    assert missing_dependencies([task]) == {"a": ["ghost"]}
    apply_blocks([task])
    assert task.status == TaskStatus.BLOCKED
    assert "ghost" in task.blocked_reason


def test_phase_labels():
    assert project_phase([]) == "EMPTY"
    assert project_phase([Task(id="a", title="A")]) == "ACTIVE"
    assert project_phase([Task(id="a", title="A", status=TaskStatus.DONE)]) == "DONE"


def test_self_dependency_does_not_crash():
    task = Task(id="a", title="A", dependencies=["a"])
    apply_blocks([task])
    assert task.status == TaskStatus.BLOCKED
