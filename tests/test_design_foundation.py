import json

import pytest

from altai.design import DesignSystemBuilder, ProductArchitect, generate_design_foundation
from altai.intelligence import ProjectModel, save_model


def _model(root, confirmed=True):
    model = ProjectModel(
        root=root,
        name="focus",
        purpose="Help developers finish interrupted projects",
        target_user="Developers using a coding agent",
        core_flow=["Select a project", "Review missing work", "Complete and verify tasks"],
        non_goals=["Deploy automatically"],
    )
    model.needs_review = [] if confirmed else ["target_user"]
    return model


def test_product_architect_uses_only_confirmed_project_context(tmp_path):
    architecture = ProductArchitect(_model(tmp_path)).build()

    assert architecture["product"]["purpose"] == "Help developers finish interrupted projects"
    assert architecture["product"]["target_users"] == ["Developers using a coding agent"]
    assert architecture["core_modules"] == [
        "Select a project",
        "Review missing work",
        "Complete and verify tasks",
    ]
    assert architecture["constraints"] == ["Deploy automatically"]
    assert architecture["decision_policy"]["requires_user"]


def test_product_architect_rejects_unconfirmed_context(tmp_path):
    with pytest.raises(ValueError, match="must be confirmed"):
        ProductArchitect(_model(tmp_path, confirmed=False)).build()


def test_design_system_preserves_declared_brand_tokens(tmp_path):
    path = tmp_path / ".altai" / "design" / "design-system.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"colors": {"primary": "#123456"}, "brand": {"name": "Acme"}}),
        encoding="utf-8",
    )

    system = DesignSystemBuilder(tmp_path).build()

    assert system["colors"]["primary"] == "#123456"
    assert system["colors"]["background"] == "#0B0F14"
    assert system["brand"]["name"] == "Acme"


def test_foundation_writes_deterministic_artifacts(tmp_path):
    save_model(_model(tmp_path))

    paths = generate_design_foundation(tmp_path)
    first = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    generate_design_foundation(tmp_path)
    second = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}

    assert first == second
    assert json.loads(first["product_architecture"])["product"]["name"] == "focus"
    assert json.loads(first["design_system"])["accessibility"]["minimum_target_size"] == 24


def test_design_system_refuses_invalid_existing_data(tmp_path):
    path = tmp_path / ".altai" / "design" / "design-system.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid"):
        DesignSystemBuilder(tmp_path).build()
