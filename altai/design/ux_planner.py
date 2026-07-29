from __future__ import annotations

from pathlib import Path
from typing import Any

from ..memory import atomic_write_text
from .product_architect import design_path

USER_FLOWS_FILENAME = "user-flows.md"


class UXPlanner:
    def __init__(self, architecture: dict[str, Any]):
        self.architecture = architecture

    def build(self) -> dict[str, list[str]]:
        modules = self.architecture.get("core_modules", [])
        if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
            raise ValueError("Product architecture core_modules must be a list of strings")
        steps = [item.strip() for item in modules if item.strip()]
        if not steps:
            steps = [
                "Open the project",
                "Review the primary goal",
                "Complete the primary goal",
                "Review the result and evidence",
            ]
        return {"primary_flow": steps}

    def render(self) -> str:
        product = self.architecture.get("product", {})
        name = product.get("name", "Project") if isinstance(product, dict) else "Project"
        steps = self.build()["primary_flow"]
        lines = [f"# {name} user flows", "", "## Primary flow", ""]
        for index, step in enumerate(steps, 1):
            lines.append(f"{index}. {step}")
        lines.extend(
            [
                "",
                "## Flow rules",
                "",
                "- Keep the primary action visible and unambiguous.",
                "- Preserve user input when an error occurs.",
                "- Provide loading, empty, error, and completed states.",
                "- Keep every step keyboard reachable.",
                "- Do not introduce a screen that does not advance the primary goal.",
                "",
            ]
        )
        return "\n".join(lines)

    def write(self, root: Path) -> Path:
        path = design_path(root, USER_FLOWS_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write_text(path, self.render(), prefix=".user-flows-")
