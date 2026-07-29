from __future__ import annotations

from pathlib import Path

from ..intelligence import load_model
from ..memory import atomic_write_text, workspace_path
from .design_system_builder import DesignSystemBuilder
from .product_architect import ProductArchitect
from .screen_generator import ScreenGenerator
from .ui_reviewer import UIReviewer
from .ux_planner import UXPlanner

DESIGN_BENCHMARK_FILENAME = "design-benchmark.md"

DESIGN_BENCHMARK_TEMPLATE = """# Design benchmark

Status: host research required

Research current, comparable products without copying their interface. Record sources,
access dates, and decisions for:

- Successful products serving the same primary job
- Developer-tool information hierarchy
- Dashboard interaction patterns
- WCAG 2.2 accessibility requirements
- Mobile and desktop layout behavior
- Dark and light theme implementation

For every source answer:

1. Which layout patterns help the primary task?
2. Which information appears first, and why?
3. Where do users struggle?
4. Which features add unnecessary complexity?
5. What will this project adopt or reject?
"""


def _write_benchmark_brief(root: Path) -> Path:
    path = workspace_path(root) / "research" / DESIGN_BENCHMARK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    return atomic_write_text(path, DESIGN_BENCHMARK_TEMPLATE, prefix=".design-benchmark-")


def generate_design_plan(root: Path) -> dict[str, Path]:
    root = Path(root).resolve()
    model = load_model(root)
    if model is None:
        raise ValueError("Project model not found. Run `altai start` before design.")

    architect = ProductArchitect(model)
    architecture = architect.build()
    screen_generator = ScreenGenerator(architecture)
    screens = screen_generator.build()
    reviewer = UIReviewer(screens)
    reviewer.require_pass()

    paths = {
        "product_architecture": architect.write(),
        "user_flows": UXPlanner(architecture).write(root),
        "screen_architecture": screen_generator.write(root),
        "design_system": DesignSystemBuilder(root).write(),
        "ui_review": reviewer.write(root),
        "design_benchmark": _write_benchmark_brief(root),
    }
    return paths
