import json
import subprocess
import sys

import pytest

from altai.cli import main
from altai.memory import load_state
from altai.models import TaskStatus
from altai.orchestrator import bootstrap, skip_task


def _settle_gaps(project):
    """The bare fixture has no README, so the purpose-confirmation gap task is
    open and, being independent of research-project, sorts before it. Settle
    it in tests that assume research-project is the only/first ready task."""
    for task in load_state(project).tasks:
        if task.id.startswith("gap-"):
            skip_task(project, task.id, "not relevant to this test")


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    (tmp_path / "app.py").write_text("# TODO: add login\n", encoding="utf-8")
    return tmp_path


def run(*argv):
    return main(list(argv))


def test_module_entry_point_actually_runs(project):
    """`python -m altai.cli` must do something; v0.1 exited 0 and did nothing."""
    result = subprocess.run(
        [sys.executable, "-m", "altai.cli", "start", str(project)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Proje:" in result.stdout
    assert (project / ".altai" / "project-state.json").exists()


def test_agent_task_file_is_clean_markdown(project):
    bootstrap(project)
    content = (project / ".altai" / "AGENT_TASK.md").read_text(encoding="utf-8")
    assert '\n        "' not in content, "broken string concatenation leaked into output"
    assert "\\n" not in content
    assert content.startswith("# ALTAI Autonomous Completion Loop")


def test_status_without_state_fails_cleanly(project, capsys):
    assert run("status", "--path", str(project)) == 1
    assert "altai start" in capsys.readouterr().err


def test_done_requires_evidence_and_dependencies(project):
    state = bootstrap(project)
    todo = next(task for task in state.tasks if task.id.startswith("todo-"))

    # Blocked by unmet dependencies (quality-gates chain).
    assert run("done", todo.id, "-e", "x", "--path", str(project)) == 1

    assert run("done", "research-project", "-e", "read docs", "--path", str(project)) == 0
    assert run("done", "quality-gates", "-e", "pytest -> ok", "--path", str(project)) == 0
    assert run("done", todo.id, "-e", "pytest -> ok", "--path", str(project)) == 0

    saved = load_state(project)
    assert saved.task(todo.id).status == TaskStatus.DONE
    assert saved.task(todo.id).evidence == ["pytest -> ok"]


def test_progress_survives_rescan_through_cli(project):
    bootstrap(project)
    run("done", "research-project", "-e", "read docs", "--path", str(project))
    run("start", str(project))
    assert load_state(project).task("research-project").status == TaskStatus.DONE


def test_fail_increments_and_eventually_blocks(project, capsys):
    bootstrap(project)
    _settle_gaps(project)
    for _ in range(3):
        run("fail", "research-project", "-r", "boom", "--path", str(project))
    saved = load_state(project)
    task = saved.task("research-project")
    assert task.attempts == 3
    assert task.status == TaskStatus.BLOCKED
    assert task.blocked_reason, "a blocked task must always carry a reason"
    output = capsys.readouterr().out
    assert "BLOKLU" in output
    assert "sebep kayitli degil" not in output


def test_block_and_unblock(project):
    bootstrap(project)
    run("block", "research-project", "-r", "needs credentials", "--path", str(project))
    assert load_state(project).task("research-project").status == TaskStatus.BLOCKED
    run("unblock", "research-project", "--path", str(project))
    assert load_state(project).task("research-project").status == TaskStatus.UNKNOWN


def test_next_returns_one_task_with_brief(project, capsys):
    bootstrap(project)
    _settle_gaps(project)
    assert run("next", "--json", "--path", str(project)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["id"] == "research-project"
    assert payload["research"]["queries"]
    assert not any("google.com/search" in q for q in payload["research"]["queries"])


def test_add_task(project):
    bootstrap(project)
    assert run("add", "Write migration guide", "--path", str(project)) == 0
    saved = load_state(project)
    assert saved.task("write-migration-guide") is not None


def test_unknown_task_id_is_a_clean_error(project, capsys):
    bootstrap(project)
    assert run("done", "nope", "-e", "x", "--path", str(project)) == 1
    assert "Unknown task id" in capsys.readouterr().err
