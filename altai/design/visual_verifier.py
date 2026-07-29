from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text
from .product_architect import design_path

VISUAL_VERIFICATION_FILENAME = "visual-verification.json"


class VisualVerifier:
    """Validate evidence supplied by the host agent; never launches a browser."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def review(self, evidence: dict[str, Any]) -> dict[str, Any]:
        raw_screenshots = evidence.get("screenshots", [])
        screenshots = raw_screenshots if isinstance(raw_screenshots, list) else []
        screenshot_paths = [
            str(Path(path) if Path(path).is_absolute() else self.root / path)
            for path in screenshots
            if isinstance(path, str) and path
        ]
        checks = {
            "build_success": evidence.get("build_success") is True,
            "screenshots_exist": bool(screenshot_paths)
            and all(Path(path).is_file() for path in screenshot_paths),
            "mobile_width_passed": evidence.get("mobile_width_passed") is True,
            "console_has_no_errors": evidence.get("console_errors") == [],
            "primary_flow_passed": evidence.get("primary_flow_passed") is True,
        }
        missing = [name for name, passed in checks.items() if not passed]
        return {
            "schema_version": 1,
            "passed": not missing,
            "checks": checks,
            "screenshots": screenshot_paths,
            "missing": missing,
            "note": "Evidence is supplied by Claude Code or Codex; ALTAI did not run the UI.",
        }

    def require_pass(self, evidence: dict[str, Any]) -> dict[str, Any]:
        report = self.review(evidence)
        if not report["passed"]:
            raise ValueError("Visual verification is incomplete: " + ", ".join(report["missing"]))
        return report

    def write(self, evidence: dict[str, Any]) -> Path:
        path = design_path(self.root, VISUAL_VERIFICATION_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.review(evidence), ensure_ascii=False, indent=2) + "\n"
        return atomic_write_text(path, payload, prefix=".visual-verification-")
