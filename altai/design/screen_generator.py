from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text
from .product_architect import design_path

SCREEN_ARCHITECTURE_FILENAME = "screen-architecture.json"

SCREEN_TEMPLATES = (
    {
        "id": "project-selection",
        "keywords": ("select", "choose", "open", "install", "folder"),
        "screen": "Project Selection",
        "purpose": "Choose or open the project that will be worked on.",
        "primary_action": "Select project",
        "components": ["project picker", "recent projects", "validation message"],
    },
    {
        "id": "project-dashboard",
        "keywords": ("scan", "analy", "overview", "status", "review"),
        "screen": "Project Dashboard",
        "purpose": "Show project health, progress, and the next useful action.",
        "primary_action": "Start or continue",
        "components": ["quality score", "progress summary", "active task", "blocked work"],
    },
    {
        "id": "task-graph",
        "keywords": ("task", "graph", "missing", "plan", "depend"),
        "screen": "Task Graph",
        "purpose": "Explain work order, dependencies, and blocked tasks.",
        "primary_action": "Inspect next task",
        "components": ["dependency graph", "task filters", "task details"],
    },
    {
        "id": "research",
        "keywords": ("research", "source", "benchmark"),
        "screen": "Research",
        "purpose": "Present relevant sources, decisions, and compatibility risks.",
        "primary_action": "Review decision",
        "components": ["source list", "decision summary", "risk notes"],
    },
    {
        "id": "live-work",
        "keywords": ("complete", "execute", "implement", "work", "run"),
        "screen": "Live Work",
        "purpose": "Show the active task and its current progress.",
        "primary_action": "Pause or continue",
        "components": ["active step", "activity log", "pause control"],
    },
    {
        "id": "test-evidence",
        "keywords": ("verify", "test", "evidence", "result", "quality"),
        "screen": "Test and Evidence",
        "purpose": "Show whether the work is proven complete.",
        "primary_action": "Review evidence",
        "components": ["gate results", "screenshots", "console status", "flow result"],
    },
)


def _screen(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": template["id"],
        "screen": template["screen"],
        "purpose": template["purpose"],
        "primary_action": template["primary_action"],
        "components": list(template["components"]),
        "states": ["loading", "empty", "error", "ready", "completed"],
        "responsive": {
            "mobile": "Single column; primary action remains visible.",
            "desktop": "Use available width without separating action from context.",
        },
        "accessibility": [
            "Logical heading order",
            "Visible keyboard focus",
            "Programmatic labels for controls",
            "Status changes announced without relying on color alone",
        ],
    }


class ScreenGenerator:
    def __init__(self, architecture: dict[str, Any]):
        self.architecture = architecture

    def build(self) -> dict[str, Any]:
        modules = self.architecture.get("core_modules", [])
        if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
            raise ValueError("Product architecture core_modules must be a list of strings")
        context = " ".join(modules).lower()
        screens = [
            _screen(template)
            for template in SCREEN_TEMPLATES
            if any(keyword in context for keyword in template["keywords"])
        ]
        if not screens:
            screens = [
                _screen(SCREEN_TEMPLATES[1]),
                _screen(SCREEN_TEMPLATES[4]),
                _screen(SCREEN_TEMPLATES[5]),
            ]

        constraints = self.architecture.get("constraints", [])
        policy = self.architecture.get("decision_policy", {})
        if constraints or (isinstance(policy, dict) and policy.get("requires_user")):
            screens.append(
                {
                    "id": "settings-safety",
                    "screen": "Settings and Safety",
                    "purpose": "Make preferences, limits, and human approval points explicit.",
                    "primary_action": "Save settings",
                    "components": ["preferences", "safety limits", "approval requirements"],
                    "states": ["loading", "error", "ready", "saved"],
                    "responsive": {
                        "mobile": "Stack settings groups in one column.",
                        "desktop": "Use a narrow readable settings column.",
                    },
                    "accessibility": [
                        "Every field has a persistent label",
                        "Errors identify the affected field",
                        "All controls are keyboard operable",
                    ],
                }
            )

        return {"schema_version": 1, "screens": screens}

    def write(self, root: Path) -> Path:
        path = design_path(root, SCREEN_ARCHITECTURE_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.build(), ensure_ascii=False, indent=2) + "\n"
        return atomic_write_text(path, payload, prefix=".screen-architecture-")
