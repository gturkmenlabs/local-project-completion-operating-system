from altai.intelligence import ProjectModel
from altai.intelligence.gap_analyzer import (
    CONFIRM_MODEL_ID,
    MISSING_TESTS_ID,
    NO_RUN_COMMAND_ID,
    UNDOCUMENTED_TESTS_ID,
    find_gaps,
    gap_tasks,
)
from altai.graph import next_ready_task
from altai.memory import load_state
from altai.orchestrator import block_task, bootstrap, complete_task
from altai.planner import FINAL_ID, GATES_ID, RESEARCH_ID


def _model(root, **overrides) -> ProjectModel:
    base = dict(
        purpose="Ship the thing",
        target_user="Developers",
        core_flow=["scan", "plan", "execute"],
        non_goals=["auto deploy"],
    )
    base.update(overrides)
    model = ProjectModel(root=root, name="x", **base)
    model.needs_review = []
    return model


def test_confirmed_model_with_matching_tests_and_command_has_no_gaps(tmp_path):
    model = _model(tmp_path, commands={"test": "pytest"}, tests=["tests/test_x.py"])

    assert find_gaps(model) == []


def test_unconfirmed_model_produces_confirmation_gap(tmp_path):
    model = _model(tmp_path)
    model.needs_review = ["purpose", "target_user"]

    ids = [gap.id for gap in find_gaps(model)]

    assert CONFIRM_MODEL_ID in ids
    gap = next(g for g in find_gaps(model) if g.id == CONFIRM_MODEL_ID)
    assert "purpose" in gap.description
    assert "target_user" in gap.description


def test_declared_test_command_without_tests_is_a_gap(tmp_path):
    model = _model(tmp_path, commands={"test": "pytest"}, tests=[])

    ids = [gap.id for gap in find_gaps(model)]

    assert MISSING_TESTS_ID in ids
    assert UNDOCUMENTED_TESTS_ID not in ids


def test_tests_without_a_command_is_a_gap(tmp_path):
    model = _model(tmp_path, commands={}, tests=["tests/test_x.py"])

    ids = [gap.id for gap in find_gaps(model)]

    assert UNDOCUMENTED_TESTS_ID in ids
    assert MISSING_TESTS_ID not in ids
    # No entry points declared, so the run-command gap must not also fire.
    assert NO_RUN_COMMAND_ID not in ids


def test_entry_point_without_any_command_is_a_gap(tmp_path):
    model = _model(tmp_path, entry_points=["app.py"], commands={})

    ids = [gap.id for gap in find_gaps(model)]

    assert NO_RUN_COMMAND_ID in ids


def test_entry_point_with_some_command_is_not_a_gap(tmp_path):
    model = _model(tmp_path, entry_points=["app.py"], commands={"build": "make build"})

    ids = [gap.id for gap in find_gaps(model)]

    assert NO_RUN_COMMAND_ID not in ids


def test_gap_tasks_are_discovered_and_stably_identified(tmp_path):
    model = _model(tmp_path, commands={"test": "pytest"}, tests=[])

    tasks = gap_tasks(model)

    assert len(tasks) == 1
    assert tasks[0].id == MISSING_TESTS_ID
    assert tasks[0].discovered is True
    assert tasks[0].acceptance


def test_bootstrap_opens_gap_tasks_for_a_purposeless_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")

    state = bootstrap(tmp_path)

    ids = {task.id for task in state.tasks}
    assert CONFIRM_MODEL_ID in ids
    assert NO_RUN_COMMAND_ID in ids


def test_gap_clears_itself_once_the_model_is_confirmed(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndescription='Does a thing'\n", encoding="utf-8"
    )
    state = bootstrap(tmp_path)
    assert NO_RUN_COMMAND_ID not in {t.id for t in state.tasks}  # no entry point in this fixture

    from altai.intelligence import load_model, save_model

    model = load_model(tmp_path)
    model.purpose = "Does a thing, confirmed"
    model.target_user = "Developers"
    model.core_flow = ["run it"]
    model.non_goals = ["nothing yet"]
    model.derived = [f for f in model.derived if f not in ("purpose",)]
    save_model(model)

    state = bootstrap(tmp_path)

    assert CONFIRM_MODEL_ID not in {t.id for t in state.tasks}


def test_confirm_model_gap_is_ready_before_quality_gates(tmp_path):
    """Confirming the project's purpose must not wait on quality gates being
    established first — that ordering would have the agent research and set up
    tooling before it even knows what the project is for."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    state = bootstrap(tmp_path)

    gap = state.task(CONFIRM_MODEL_ID)
    assert gap is not None
    assert GATES_ID not in gap.dependencies

    ready = next_ready_task(state.tasks)
    assert ready is not None and ready.id == CONFIRM_MODEL_ID


def test_confirm_model_gap_still_gates_final_verification(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    state = bootstrap(tmp_path)

    final = state.task(FINAL_ID)
    assert CONFIRM_MODEL_ID in final.dependencies


def test_quality_gates_waits_on_confirm_model_gap(tmp_path):
    """The other half of purpose-first: exempting confirm-model from
    depending on quality-gates only means confirmation doesn't wait on gates.
    Gates must still wait on confirmation, or nothing actually stops the
    agent from researching and establishing tooling before it knows what the
    project is for — the ordering would be cosmetic, not enforced."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    state = bootstrap(tmp_path)

    gates = state.task(GATES_ID)
    assert CONFIRM_MODEL_ID in gates.dependencies


def test_blocked_confirm_model_gap_makes_quality_gates_unreachable(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    bootstrap(tmp_path)
    complete_task(tmp_path, RESEARCH_ID, ["docs read"])
    block_task(tmp_path, CONFIRM_MODEL_ID, "waiting on user to confirm purpose")

    state = load_state(tmp_path)
    ready = next_ready_task(state.tasks)

    assert ready is None or ready.id != GATES_ID


def test_final_verification_waits_on_open_gaps(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    bootstrap(tmp_path)

    import pytest

    with pytest.raises(ValueError, match="unmet dependencies"):
        complete_task(tmp_path, FINAL_ID, ["done"])
