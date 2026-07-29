from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..intelligence import ProjectModel, load_model
from ..memory import atomic_write_text, workspace_path

PRODUCT_ARCHITECTURE_FILENAME = "product-architecture.json"

AUTONOMOUS_DECISIONS = [
    "Prioritize the primary user's main goal.",
    "Complete the core job with the fewest screens.",
    "Preserve an existing brand language.",
    "Create a restrained design system when no brand language exists.",
    "Do not add features only because they are popular.",
    "Remove elements that make the primary flow harder.",
    "Make reversible design decisions without interrupting the user.",
]

USER_DECISIONS = [
    "Change the logo or brand name.",
    "Replace the primary target user.",
    "Use a paid design tool or service.",
    "Make a legal or corporate brand decision.",
]


def design_path(root: Path, filename: str) -> Path:
    return workspace_path(root) / "design" / filename


class ProductArchitect:
    def __init__(self, model: ProjectModel):
        self.model = model

    def build(self) -> dict[str, Any]:
        if self.model.needs_review:
            fields = ", ".join(self.model.needs_review)
            raise ValueError(f"Project model must be confirmed before design: {fields}")
        if not self.model.purpose or not self.model.target_user:
            raise ValueError("Project model needs a purpose and target user before design")

        return {
            "schema_version": 1,
            "product": {
                "name": self.model.name,
                "purpose": self.model.purpose,
                "target_users": [self.model.target_user],
            },
            "core_modules": list(self.model.core_flow),
            "constraints": list(self.model.non_goals),
            "decision_policy": {
                "autonomous": list(AUTONOMOUS_DECISIONS),
                "requires_user": list(USER_DECISIONS),
            },
        }

    def write(self) -> Path:
        path = design_path(self.model.root, PRODUCT_ARCHITECTURE_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.build(), ensure_ascii=False, indent=2) + "\n"
        return atomic_write_text(path, payload, prefix=".product-architecture-")


def generate_design_foundation(
    root: Path, brand_tokens: dict[str, Any] | None = None
) -> dict[str, Path]:
    from .design_system_builder import DesignSystemBuilder

    root = Path(root).resolve()
    model = load_model(root)
    if model is None:
        raise ValueError("Project model not found. Run `altai start` before design.")

    architecture_path = ProductArchitect(model).write()
    system_path = DesignSystemBuilder(root).write(brand_tokens)
    return {
        "product_architecture": architecture_path,
        "design_system": system_path,
    }
