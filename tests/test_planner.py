from altai.graph import find_cycles
from altai.models import ProjectState, Task
from altai.planner import FINAL_ID, GATES_ID, RESEARCH_ID, enrich_plan


def _state(tmp_path, *tasks):
    return ProjectState(root=tmp_path, name="x", tasks=list(tasks))


def test_scaffolding_is_injected(tmp_path):
    state = enrich_plan(_state(tmp_path))
    assert [task.id for task in state.tasks] == [RESEARCH_ID, GATES_ID, FINAL_ID]


def test_enrich_plan_is_idempotent(tmp_path):
    state = _state(tmp_path, Task(id="todo-1", title="A"))
    first = enrich_plan(state)
    ids_before = [task.id for task in first.tasks]
    deps_before = {task.id: list(task.dependencies) for task in first.tasks}

    second = enrich_plan(first)
    assert [task.id for task in second.tasks] == ids_before
    assert {task.id: task.dependencies for task in second.tasks} == deps_before


def test_plan_has_no_cycles(tmp_path):
    state = enrich_plan(_state(tmp_path, Task(id="todo-1", title="A"), Task(id="todo-2", title="B")))
    assert find_cycles(state.tasks) == []


def test_final_verification_gates_on_everything(tmp_path):
    state = enrich_plan(_state(tmp_path, Task(id="todo-1", title="A")))
    final = state.task(FINAL_ID)
    assert set(final.dependencies) == {RESEARCH_ID, GATES_ID, "todo-1"}
