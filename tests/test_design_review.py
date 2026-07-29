import json

import pytest

from altai.design import ScreenGenerator, UIReviewer, VisualVerifier


def _architecture():
    return {
        "core_modules": ["Open project", "Complete work", "Test and record evidence"],
        "constraints": [],
        "decision_policy": {},
    }


def test_generated_screens_pass_pre_code_review(tmp_path):
    screens = ScreenGenerator(_architecture()).build()

    path = UIReviewer(screens).write(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["passed"] is True
    assert all(screen["passed"] for screen in report["screens"])


def test_ui_review_rejects_missing_interaction_requirements():
    screens = {
        "screens": [
            {
                "screen": "Dashboard",
                "purpose": "Show status",
                "components": ["status"],
                "states": ["ready"],
                "responsive": {"desktop": "Wide"},
                "accessibility": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="Primary action"):
        UIReviewer(screens).require_pass()


def test_visual_verifier_requires_all_host_evidence(tmp_path):
    screenshot = tmp_path / "dashboard.png"
    screenshot.write_bytes(b"image")
    evidence = {
        "build_success": True,
        "screenshots": ["dashboard.png"],
        "mobile_width_passed": True,
        "console_errors": [],
        "primary_flow_passed": True,
    }

    report = VisualVerifier(tmp_path).review(evidence)

    assert report["passed"] is True
    assert report["checks"]["screenshots_exist"] is True


def test_visual_verifier_reports_missing_checks(tmp_path):
    report = VisualVerifier(tmp_path).review(
        {
            "build_success": False,
            "screenshots": [],
            "mobile_width_passed": False,
            "console_errors": ["TypeError"],
            "primary_flow_passed": False,
        }
    )

    assert report["passed"] is False
    assert set(report["missing"]) == {
        "build_success",
        "screenshots_exist",
        "mobile_width_passed",
        "console_has_no_errors",
        "primary_flow_passed",
    }
