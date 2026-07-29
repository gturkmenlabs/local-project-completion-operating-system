from altai.autopilot import EXIT_BLOCKED, EXIT_COMPLETE, EXIT_OK, EXIT_POLICY_HOLD, run_autopilot
from altai.cli import main
from altai.memory import load_state
from altai.orchestrator import (
    add_task,
    block_task,
    complete_task,
    promote_opportunity,
    skip_task,
)
from altai.planner import FINAL_ID, GATES_ID, RESEARCH_ID


def _bare_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndescription='does a thing'\n", encoding="utf-8"
    )
    return tmp_path


def _settle_confirmation_gap(root):
    # The bare fixture has no README (opens the purpose-confirmation gap) and
    # no TODO markers (opens the baseline-verification scaffold task). Settle
    # both so tests can reason about one specific task in isolation.
    for task in load_state(root).tasks:
        if task.id.startswith("gap-") or task.id == "baseline-verification":
            skip_task(root, task.id, "not relevant to this test")


def test_autopilot_returns_a_ready_task_with_ok_exit(tmp_path):
    root = _bare_project(tmp_path)

    report = run_autopilot(root)

    assert report.exit_code == EXIT_OK
    assert report.task is not None
    assert report.policy_flags == []


def test_autopilot_flags_a_policy_matching_task(tmp_path):
    root = _bare_project(tmp_path)
    run_autopilot(root)
    _settle_confirmation_gap(root)
    complete_task(root, RESEARCH_ID, ["docs read"])
    complete_task(root, GATES_ID, ["pytest -> ok"])
    add_task(root, "delete all rows from the legacy table", task_id="cleanup")

    report = run_autopilot(root, rescan=False)

    assert report.task["task"]["id"] == "cleanup"
    assert "destructive" in report.policy_flags
    assert report.exit_code == EXIT_POLICY_HOLD


def test_autopilot_reports_blocked_when_nothing_is_ready(tmp_path):
    root = _bare_project(tmp_path)
    run_autopilot(root)
    _settle_confirmation_gap(root)
    block_task(root, RESEARCH_ID, "stuck")

    report = run_autopilot(root, rescan=False)

    assert report.task is None
    assert report.phase == "BLOCKED"
    assert report.exit_code == EXIT_BLOCKED
    assert report.blocked and report.blocked[0]["id"] == RESEARCH_ID


def test_autopilot_reports_complete_when_project_is_done(tmp_path):
    root = _bare_project(tmp_path)
    run_autopilot(root)
    _settle_confirmation_gap(root)
    complete_task(root, RESEARCH_ID, ["docs read"])
    complete_task(root, GATES_ID, ["pytest -> ok"])
    complete_task(root, FINAL_ID, ["build ok"])

    report = run_autopilot(root, rescan=False)

    assert report.exit_code == EXIT_COMPLETE
    assert report.phase == "DONE"


def test_autopilot_includes_top_opportunities(tmp_path):
    root = _bare_project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "app.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")

    report = run_autopilot(root)

    assert any(c["kind"] == "large-function" for c in report.opportunities)


def test_autopilot_single_rescan_per_call(tmp_path, monkeypatch):
    root = _bare_project(tmp_path)
    calls = []
    import altai.autopilot as autopilot_module

    original = autopilot_module.bootstrap

    def counting_bootstrap(*args, **kwargs):
        calls.append(kwargs.get("rescan", args[1] if len(args) > 1 else True))
        return original(*args, **kwargs)

    monkeypatch.setattr(autopilot_module, "bootstrap", counting_bootstrap)
    run_autopilot(root)

    assert len(calls) == 1


def test_promote_creates_a_task_and_removes_the_opportunity(tmp_path):
    root = _bare_project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "app.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")
    report = run_autopilot(root)
    candidate = next(c for c in report.opportunities if c["kind"] == "large-function")

    state, task = promote_opportunity(root, candidate["id"])

    assert task.id == candidate["id"]
    assert task.discovered is False
    assert state.task(candidate["id"]) is not None
    from altai.intelligence.opportunity_finder import load_opportunities

    remaining_ids = {c.id for c in load_opportunities(root)}
    assert candidate["id"] not in remaining_ids


def test_promote_reopens_final_verification(tmp_path):
    root = _bare_project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "app.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")
    report = run_autopilot(root)
    _settle_confirmation_gap(root)
    candidate = next(c for c in report.opportunities if c["kind"] == "large-function")
    complete_task(root, RESEARCH_ID, ["docs read"])
    complete_task(root, GATES_ID, ["pytest -> ok"])
    complete_task(root, FINAL_ID, ["build ok"])
    assert load_state(root).task(FINAL_ID).status.value == "done"

    promote_opportunity(root, candidate["id"])

    assert load_state(root).task(FINAL_ID).status.value != "done"


def test_promote_unknown_id_raises(tmp_path):
    root = _bare_project(tmp_path)
    run_autopilot(root)
    import pytest

    with pytest.raises(ValueError, match="Unknown opportunity id"):
        promote_opportunity(root, "opp-doesnotexist")


def test_cli_autopilot_and_promote(tmp_path, capsys):
    root = _bare_project(tmp_path)
    body = "\n".join(f"    x{i} = {i}" for i in range(80))
    (root / "app.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")

    exit_code = main(["autopilot", "--json", "--path", str(root)])
    assert exit_code in (EXIT_OK, EXIT_BLOCKED, EXIT_COMPLETE, EXIT_POLICY_HOLD)
    import json

    payload = json.loads(capsys.readouterr().out)
    assert "opportunities" in payload
    candidate_id = next(c["id"] for c in payload["opportunities"] if c["kind"] == "large-function")

    assert main(["promote", candidate_id, "--path", str(root)]) == 0
    capsys.readouterr()
    assert load_state(root).task(candidate_id) is not None
