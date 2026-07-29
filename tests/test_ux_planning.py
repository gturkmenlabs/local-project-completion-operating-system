import json

from altai.design import ScreenGenerator, UXPlanner


def _architecture(modules=None):
    return {
        "product": {"name": "ALTAI"},
        "core_modules": modules
        or [
            "Select a project",
            "Scan and review project status",
            "Build a dependency task graph",
            "Research the active task",
            "Complete work",
            "Test and record evidence",
        ],
        "constraints": ["Do not publish automatically"],
        "decision_policy": {"requires_user": ["Change the brand"]},
    }


def test_ux_planner_turns_core_modules_into_primary_flow(tmp_path):
    planner = UXPlanner(_architecture(["Choose project", "Complete work", "Review evidence"]))

    path = planner.write(tmp_path)
    content = path.read_text(encoding="utf-8")

    assert "1. Choose project" in content
    assert "3. Review evidence" in content
    assert "loading, empty, error, and completed states" in content


def test_screen_generator_selects_only_relevant_screens():
    result = ScreenGenerator(_architecture(["Open folder", "Complete work", "Test result"])).build()
    ids = [screen["id"] for screen in result["screens"]]

    assert ids == ["project-selection", "live-work", "test-evidence", "settings-safety"]


def test_altai_flow_generates_complete_minimum_architecture(tmp_path):
    path = ScreenGenerator(_architecture()).write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [screen["id"] for screen in payload["screens"]]

    assert ids == [
        "project-selection",
        "project-dashboard",
        "task-graph",
        "research",
        "live-work",
        "test-evidence",
        "settings-safety",
    ]
    assert all(screen["primary_action"] for screen in payload["screens"])
    assert all(screen["responsive"] for screen in payload["screens"])
    assert all(screen["accessibility"] for screen in payload["screens"])
