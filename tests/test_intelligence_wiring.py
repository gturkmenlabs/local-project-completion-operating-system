"""End-to-end checks that code_graph and project_memory actually feed the loop,
not just that each module works in isolation."""

from altai.cli import main
from altai.intelligence.gap_analyzer import CONFIRM_MODEL_ID
from altai.orchestrator import add_task, bootstrap, complete_task, learn, next_brief, skip_task
from altai.memory import load_state
from altai.planner import BENCHMARK_ID, GATES_ID, RESEARCH_ID


def _project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "auth.py").write_text("def login():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_next_brief_includes_related_files_from_code_graph(tmp_path):
    root = _project(tmp_path)
    bootstrap(root)
    complete_task(root, RESEARCH_ID, ["docs read"])
    complete_task(root, BENCHMARK_ID, ["3 sources recorded, 2 adopted"])
    # quality-gates now waits on the purpose-confirmation gap (it must not be
    # possible to establish gates before the project's purpose is confirmed),
    # so that has to be settled before quality-gates can complete. The bare
    # fixture also has no TODO markers, opening a baseline-verification task
    # that would otherwise compete with the task under test for
    # next_ready_task. Settle both.
    for stale_id in (CONFIRM_MODEL_ID, "baseline-verification"):
        if load_state(root).task(stale_id) is not None:
            skip_task(root, stale_id, "not relevant to this test")
    complete_task(root, GATES_ID, ["pytest -> ok"])
    # A hand-added task whose text names no file — the only way related_files
    # is proven to come from the code graph's symbol match, not from a path
    # already sitting in the task's own description (a discovered TODO task's
    # description is literally "auth.py:N", which would match trivially).
    _, task = add_task(root, "fix login redirect bug")
    state = load_state(root)

    brief = next_brief(state)

    assert brief["task"]["id"] == task.id
    assert "auth.py" in brief["related_files"]


def test_next_brief_includes_memory_digest_once_something_is_recorded(tmp_path):
    root = _project(tmp_path)
    bootstrap(root)
    learn(root, "architecture", "orchestrator owns final decisions")
    state = load_state(root)

    brief = next_brief(state)

    assert "memory" in brief
    assert "orchestrator owns final decisions" in brief["memory"]


def test_agent_task_md_surfaces_memory_once_recorded(tmp_path):
    root = _project(tmp_path)
    bootstrap(root)
    text_before = (root / ".altai" / "AGENT_TASK.md").read_text(encoding="utf-8")
    assert "Project memory" not in text_before

    learn(root, "failed-approaches", "global session state broke concurrency")
    bootstrap(root)

    text_after = (root / ".altai" / "AGENT_TASK.md").read_text(encoding="utf-8")
    assert "Project memory" in text_after
    assert "global session state broke concurrency" in text_after


def test_cli_learn_and_rule_commands(tmp_path, capsys):
    root = _project(tmp_path)
    bootstrap(root)

    assert main(["learn", "coding-conventions", "use dataclasses for models", "--path", str(root)]) == 0
    assert main(["rule", "touching billing code", "get sign-off first", "--path", str(root)]) == 0
    capsys.readouterr()

    memory_path = root / ".altai" / "memory" / "coding-conventions.md"
    assert "use dataclasses for models" in memory_path.read_text(encoding="utf-8")
    rules_path = root / ".altai" / "memory" / "learned-rules.json"
    assert "touching billing code" in rules_path.read_text(encoding="utf-8")
