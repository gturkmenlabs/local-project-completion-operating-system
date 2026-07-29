from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text
from .product_architect import design_path

UI_REVIEW_FILENAME = "ui-review.json"


class UIReviewer:
    def __init__(self, screen_architecture: dict[str, Any]):
        self.screen_architecture = screen_architecture

    def review(self) -> dict[str, Any]:
        screens = self.screen_architecture.get("screens", [])
        if not isinstance(screens, list) or not screens:
            return {
                "schema_version": 1,
                "passed": False,
                "screens": [],
                "issues": ["At least one screen is required."],
            }

        results = []
        all_issues: list[str] = []
        for screen in screens:
            if not isinstance(screen, dict):
                all_issues.append("Every screen must be an object.")
                continue
            name = str(screen.get("screen") or screen.get("id") or "Unnamed screen")
            issues = self._screen_issues(screen)
            results.append({"screen": name, "passed": not issues, "issues": issues})
            all_issues.extend(f"{name}: {issue}" for issue in issues)
        return {
            "schema_version": 1,
            "passed": not all_issues,
            "screens": results,
            "issues": all_issues,
        }

    @staticmethod
    def _screen_issues(screen: dict[str, Any]) -> list[str]:
        issues = []
        if not str(screen.get("purpose", "")).strip():
            issues.append("Purpose is missing.")
        if not str(screen.get("primary_action", "")).strip():
            issues.append("Primary action is missing.")

        components = screen.get("components", [])
        if not isinstance(components, list) or not components:
            issues.append("Components are missing.")
        elif len(components) > 7:
            issues.append("More than seven component groups obscures the visual hierarchy.")

        states = set(screen.get("states", []))
        if not {"loading", "error"}.issubset(states):
            issues.append("Loading and error states are required.")

        responsive = screen.get("responsive", {})
        if not isinstance(responsive, dict) or not {"mobile", "desktop"}.issubset(responsive):
            issues.append("Mobile and desktop behavior are required.")

        accessibility = " ".join(screen.get("accessibility", [])).lower()
        if "keyboard" not in accessibility:
            issues.append("Keyboard operation is not specified.")
        if "label" not in accessibility and "heading" not in accessibility:
            issues.append("Programmatic labels or heading hierarchy are not specified.")
        return issues

    def require_pass(self) -> dict[str, Any]:
        report = self.review()
        if not report["passed"]:
            raise ValueError("UI specification failed review: " + "; ".join(report["issues"]))
        return report

    def write(self, root: Path) -> Path:
        path = design_path(root, UI_REVIEW_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.review(), ensure_ascii=False, indent=2) + "\n"
        return atomic_write_text(path, payload, prefix=".ui-review-")
