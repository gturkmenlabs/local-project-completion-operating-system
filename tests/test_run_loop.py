import json
import shlex
import subprocess
import sys

import pytest

from altai.autonomy import FULL, GUARDED, Autonomy
from altai.cli import main
from altai.intelligence.opportunity_finder import OpportunityCandidate, load_opportunities, save_opportunities
from altai.loop import (
    EXIT_BLOCKED,
    EXIT_INCOMPLETE,
    EXIT_NO_AGENT,
    EXIT_OK,
    EXIT_POLICY_HOLD,
    run_project,
)
from altai.memory import load_state, workspace_path
from altai.orchestrator import add_task

AGENT_SCRIPT = """
import pathlib, sys
pathlib.Path("agent-ran.log").open("a", encoding="utf-8").write(sys.argv[-1][:80] + "\\n")
sys.exit({code})
"""

#: Writes a file named after the task it was given, so a test can prove a commit
#: kept the work and a rollback discarded it.
EDITING_AGENT_SCRIPT = """
import json, pathlib, re, sys
prompt = sys.argv[-1]
task = re.search(r"TASK (\\S+):", prompt).group(1)
pathlib.Path(task + ".txt").write_text("written by the agent\\n", encoding="utf-8")
print(json.dumps({{"result": "ok", "total_cost_usd": 0.25, "num_turns": 3, "session_id": "s-1"}}))
sys.exit({code})
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # The suite must not inherit the developer's own agent or autonomy settings.
    monkeypatch.delenv("ALTAI_AGENT_CMD", raising=False)
    monkeypatch.delenv("ALTAI_AUTONOMY", raising=False)


def _project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndescription='does a thing'\n", encoding="utf-8"
    )
    (tmp_path / "app.py").write_text("def f():\n    # TODO: finish this\n    return 1\n", encoding="utf-8")
    return tmp_path


def _agent(tmp_path, exit_code=0, template=AGENT_SCRIPT, name="agent"):
    # Kept outside the project root: an agent script committed by the run itself
    # would make the "what did the agent change" assertions meaningless.
    script = tmp_path.parent / f"{name}_{tmp_path.name}_{exit_code}.py"
    script.write_text(template.format(code=exit_code), encoding="utf-8")
    return shlex.join([sys.executable, str(script)])


def _editing_agent(tmp_path, exit_code=0):
    return _agent(tmp_path, exit_code, template=EDITING_AGENT_SCRIPT, name="editor")


def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )


def _repo(tmp_path):
    """A project fixture that is also a clean git repository."""
    root = _project(tmp_path)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".altai/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


def _run(root, **kwargs):
    kwargs.setdefault("allow_nested", True)
    return run_project(root, **kwargs)


def test_one_command_drives_the_project_to_done(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path))

    assert report.exit_code == EXIT_OK
    assert report.phase == "DONE"
    assert report.completed, "no task was completed"
    state = load_state(root)
    assert all(task.status.value in {"done", "skipped"} for task in state.tasks)
    # Evidence is the agent invocation itself, recorded through the normal path.
    assert any(task.evidence for task in state.tasks)
    assert (root / "agent-ran.log").exists()


def test_agent_failure_becomes_a_failed_attempt_and_then_a_block(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path, exit_code=2), max_iterations=12)

    assert report.exit_code == EXIT_BLOCKED
    assert report.blocked
    assert all(item.outcome == "failed" for item in report.iterations)
    assert "agent exited 2" in report.iterations[0].reason
    # Bounded: the task blocks itself rather than retrying forever.
    assert load_state(root).task(report.iterations[0].task_id).attempts >= 3


def test_a_red_check_refuses_to_complete_the_task(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path), checks=["exit 1"], max_iterations=4)

    assert all(item.outcome == "failed" for item in report.iterations)
    assert "custom check failed" in report.iterations[0].reason
    assert load_state(root).task(report.iterations[0].task_id).status.value != "done"


def test_declared_test_command_runs_as_the_gate(tmp_path):
    root = _project(tmp_path)
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndescription='does a thing'\n\n[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )

    report = _run(root, agent=_agent(tmp_path), max_iterations=1)

    assert [check["command"] for check in report.plan["checks"]] == ["pytest"]
    assert report.iterations[0].checks[0]["label"] == "test"


def test_plan_only_hands_the_task_back_without_executing(tmp_path):
    root = _project(tmp_path)

    report = _run(root, plan_only=True, agent=_agent(tmp_path))

    assert report.exit_code == EXIT_OK
    assert [item.outcome for item in report.iterations] == ["handoff"]
    assert not (root / "agent-ran.log").exists()


def test_missing_agent_is_reported_not_silently_ignored(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent="none")

    assert report.exit_code == EXIT_NO_AGENT
    assert "no host-agent CLI resolved" in report.notes


def test_nested_run_hands_off_instead_of_spawning_an_agent(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")

    report = run_project(root, agent=None)

    assert [item.outcome for item in report.iterations] == ["handoff"]
    assert any("nested" in note for note in report.notes)


def test_guarded_autonomy_holds_a_stop_and_ask_task(tmp_path):
    root = _project(tmp_path)
    _run(root, plan_only=True)
    add_task(root, "delete all rows from the legacy table", task_id="cleanup")

    report = _run(
        root,
        autonomy=Autonomy(GUARDED),
        agent=_agent(tmp_path),
        rescan=False,
        max_iterations=20,
    )

    assert report.exit_code == EXIT_POLICY_HOLD
    held = report.iterations[-1]
    assert held.task_id == "cleanup" and held.outcome == "held"
    assert "destructive" in held.policy_flags


def test_full_autonomy_approves_the_same_task_and_records_the_approval(tmp_path):
    root = _project(tmp_path)
    _run(root, plan_only=True)
    add_task(root, "delete all rows from the legacy table", task_id="cleanup")

    report = _run(root, autonomy=Autonomy(FULL), agent=_agent(tmp_path), rescan=False)

    approved = next(item for item in report.iterations if item.task_id == "cleanup")
    assert approved.outcome == "done"
    assert approved.auto_approved is True and "destructive" in approved.policy_flags
    log = (workspace_path(root) / "runs" / "log.md").read_text(encoding="utf-8")
    assert "auto-approved cleanup" in log
    evidence = (workspace_path(root) / "evidence" / "cleanup.md").read_text(encoding="utf-8")
    assert "auto-approved cleanup" in evidence


def test_recommendations_are_applied_without_being_asked(tmp_path):
    root = _project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "big.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")

    report = _run(root, plan_only=True)

    assert any(item["kind"] == "large-function" for item in report.applied_recommendations)
    promoted = report.applied_recommendations[0]["id"]
    assert load_state(root).task(promoted) is not None
    assert promoted not in {c.id for c in load_opportunities(root)}


def test_guarded_autonomy_still_skips_a_flagged_recommendation(tmp_path):
    root = _project(tmp_path)
    _run(root, plan_only=True, apply_recommendations=False)
    save_opportunities(
        root,
        [
            OpportunityCandidate(
                id="opp-publish",
                kind="manual",
                title="Publish the application",
                description="Deploy the application to production",
                file="",
                score=1,
            )
        ],
    )

    guarded = _run(root, plan_only=True, autonomy=Autonomy(GUARDED), rescan=False)
    assert guarded.applied_recommendations == []
    assert load_state(root).task("opp-publish") is None

    full = _run(root, plan_only=True, autonomy=Autonomy(FULL), rescan=False)
    assert [item["id"] for item in full.applied_recommendations] == ["opp-publish"]
    assert load_state(root).task("opp-publish") is not None


def test_no_apply_leaves_recommendations_pending(tmp_path):
    root = _project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "big.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")

    report = _run(root, plan_only=True, apply_recommendations=False)

    assert report.applied_recommendations == []
    assert load_opportunities(root)


def test_iteration_budget_stops_the_run_and_says_so(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path), max_iterations=1)

    assert report.exit_code == EXIT_INCOMPLETE
    assert len(report.iterations) == 1
    assert report.phase != "DONE"


def test_time_budget_stops_the_run(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path), time_budget=0.0)

    assert report.exit_code == EXIT_INCOMPLETE
    assert report.iterations == []


def test_a_missing_gate_is_disclosed_rather_than_assumed(tmp_path):
    root = _project(tmp_path)

    report = _run(root, agent=_agent(tmp_path), max_iterations=1)

    assert any("agent's exit code alone" in note for note in report.notes)


def test_design_pass_failure_does_not_abort_the_run(tmp_path):
    root = _project(tmp_path)

    report = _run(root, plan_only=True, design=True)

    # The bare fixture's model is unconfirmed, so the design pass cannot run —
    # the run still hands out real work instead of dying.
    assert report.design == {} and any("design pass skipped" in note for note in report.notes)
    assert report.iterations


def test_cli_run_json_reports_the_whole_run(tmp_path, capsys):
    root = _project(tmp_path)

    exit_code = main(["run", str(root), "--agent", _agent(tmp_path), "--allow-nested", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == EXIT_OK
    assert payload["phase"] == "DONE"
    assert payload["autonomy"] == FULL
    assert payload["completed"] == len(payload["iterations"])
    assert payload["plan"]["agent"]["name"]


def test_cli_run_safe_flag_selects_guarded_autonomy(tmp_path, capsys):
    root = _project(tmp_path)
    main(["run", str(root), "--plan-only", "--json", "--allow-nested"])
    capsys.readouterr()
    add_task(root, "delete all rows from the legacy table", task_id="cleanup")

    exit_code = main(
        ["run", str(root), "--safe", "--no-rescan", "--agent", _agent(tmp_path), "--allow-nested", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == EXIT_POLICY_HOLD
    assert payload["autonomy"] == GUARDED
    assert payload["iterations"][-1]["outcome"] == "held"


def test_each_completed_task_becomes_its_own_commit(tmp_path):
    root = _repo(tmp_path)

    report = _run(root, agent=_editing_agent(tmp_path), max_iterations=3)

    done = [item for item in report.iterations if item.outcome == "done"]
    assert done and all(item.commit for item in done)
    assert report.checkpoint["enabled"] is True and report.checkpoint["rollback"] is True
    subjects = _git(root, "log", "--pretty=%s").stdout
    assert f"altai({done[0].task_id})" in subjects
    assert len(report.commits) == len(done)


def test_a_failed_attempt_is_rolled_back_to_the_checkpoint(tmp_path):
    root = _repo(tmp_path)

    report = _run(root, agent=_editing_agent(tmp_path, exit_code=1), max_iterations=2)

    failed = report.iterations[0]
    assert failed.outcome == "failed" and failed.rolled_back is True
    assert not (root / f"{failed.task_id}.txt").exists(), "the half-finished attempt survived"
    assert _git(root, "status", "--porcelain").stdout.strip() == ""


def test_no_rollback_keeps_the_failed_attempt_for_inspection(tmp_path):
    root = _repo(tmp_path)

    report = _run(root, agent=_editing_agent(tmp_path, exit_code=1), rollback=False, max_iterations=1)

    failed = report.iterations[0]
    assert failed.rolled_back is False
    assert (root / f"{failed.task_id}.txt").exists()


def test_uncommitted_work_disables_checkpointing(tmp_path):
    root = _repo(tmp_path)
    (root / "my-wip.py").write_text("half of my own change\n", encoding="utf-8")

    report = _run(root, agent=_agent(tmp_path), max_iterations=1)

    assert report.checkpoint["enabled"] is False
    assert any("already dirty" in note for note in report.notes)
    assert (root / "my-wip.py").exists()


def test_no_commit_flag_leaves_history_alone(tmp_path):
    root = _repo(tmp_path)
    before = _git(root, "rev-parse", "HEAD").stdout.strip()

    report = _run(root, agent=_editing_agent(tmp_path), commit=False, max_iterations=2)

    assert report.commits == []
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == before


def test_agent_reported_cost_and_turns_are_totalled(tmp_path):
    root = _repo(tmp_path)

    report = _run(root, agent=_editing_agent(tmp_path), max_iterations=2)

    assert report.iterations[0].cost_usd == 0.25
    assert report.iterations[0].turns == 3
    assert report.cost_usd == round(0.25 * len(report.iterations), 4)


def test_applied_recommendations_are_written_to_project_memory(tmp_path):
    root = _project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "big.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")

    report = _run(root, plan_only=True)

    promoted = report.applied_recommendations[0]
    memory = (workspace_path(root) / "memory" / "product-decisions.md").read_text(encoding="utf-8")
    assert "auto-applied recommendation" in memory
    assert promoted["title"] in memory
    assert promoted["id"] in memory


def test_design_pass_is_attempted_by_default_and_can_be_switched_off(tmp_path):
    root = _project(tmp_path)

    attempted = _run(root, plan_only=True)
    assert any("design pass skipped" in note for note in attempted.notes)

    off = _run(root, plan_only=True, design=False, rescan=False)
    assert not any("design pass" in note for note in off.notes)


def test_max_turns_reaches_a_known_agent_cli():
    from altai.executor import detect_agent

    spec = detect_agent("claude", env={}, max_turns=42)

    assert "--max-turns" in spec.argv and "42" in spec.argv


def test_autonomy_env_default_is_full_and_overridable(monkeypatch):
    assert Autonomy.from_env(env={}).level == FULL
    assert Autonomy.from_env(env={"ALTAI_AUTONOMY": "guarded"}).level == GUARDED
    assert Autonomy.from_env("full", env={"ALTAI_AUTONOMY": "guarded"}).level == FULL
    with pytest.raises(ValueError, match="Unknown autonomy level"):
        Autonomy.from_env(env={"ALTAI_AUTONOMY": "yolo"})
