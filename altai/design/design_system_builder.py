from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text
from .product_architect import design_path

DESIGN_SYSTEM_FILENAME = "design-system.json"

DEFAULT_DESIGN_SYSTEM: dict[str, Any] = {
    "schema_version": 1,
    "colors": {
        "background": "#0B0F14",
        "surface": "#131A22",
        "text": "#F5F7FA",
        "muted_text": "#A9B4C0",
        "primary": "#9B87FF",
        "success": "#36C98F",
        "warning": "#F5B942",
        "danger": "#FF7A7A",
    },
    "spacing": {"small": 8, "medium": 16, "large": 24},
    "radius": {"card": 12, "button": 8},
    "typography": {
        "body": {"size": 16, "line_height": 1.5},
        "heading": {"size": 24, "line_height": 1.25},
    },
    "breakpoints": {"mobile": 480, "tablet": 768, "desktop": 1200},
    "accessibility": {
        "minimum_contrast": {"normal_text": 4.5, "large_text": 3.0, "ui": 3.0},
        "minimum_target_size": 24,
        "focus_indicator": {"width": 2, "offset": 2, "color": "#C9BEFF"},
    },
}


def _merge(base: dict[str, Any], declared: dict[str, Any]) -> dict[str, Any]:
    for key, value in declared.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(dict(base[key]), value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class DesignSystemBuilder:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    @property
    def path(self) -> Path:
        return design_path(self.root, DESIGN_SYSTEM_FILENAME)

    def _declared_tokens(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Existing design system is invalid: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("Existing design system must be a JSON object")
        return payload

    def build(self, brand_tokens: dict[str, Any] | None = None) -> dict[str, Any]:
        if brand_tokens is not None and not isinstance(brand_tokens, dict):
            raise TypeError("brand_tokens must be a dictionary")
        system = _merge(copy.deepcopy(DEFAULT_DESIGN_SYSTEM), self._declared_tokens())
        if brand_tokens:
            system = _merge(system, brand_tokens)
        try:
            json.dumps(system)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Design tokens must be JSON serializable: {error}") from error
        return system

    def write(self, brand_tokens: dict[str, Any] | None = None) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.build(brand_tokens), ensure_ascii=False, indent=2) + "\n"
        return atomic_write_text(self.path, payload, prefix=".design-system-")
