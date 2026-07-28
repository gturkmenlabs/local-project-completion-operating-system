import json

from altai.intelligence import ProjectModelBuilder, load_model
from altai.orchestrator import bootstrap


def _project(root):
    (root / "docs").mkdir()
    (root / "tests").mkdir()
    (root / "README.md").write_text(
        """# Focus

Focus helps developers finish interrupted projects.

## Workflow

- scan the repository
- create a task graph
- execute and verify tasks

## Non-goals

- deploy automatically
- use secrets
""",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("Run tests before completion.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("Keep changes small.\n", encoding="utf-8")
    (root / "docs" / "architecture.md").write_text(
        "# Architecture\n\nThe orchestrator owns final decisions.\n", encoding="utf-8"
    )
    (root / "tests" / "test_flow.py").write_text(
        "def test_flow():\n    assert True\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        """[project]
name = "focus-agent"
description = "Finish interrupted software projects"

[project.scripts]
focus = "focus.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        encoding="utf-8",
    )
    return root


def test_builder_derives_declared_project_context(tmp_path):
    model = ProjectModelBuilder(_project(tmp_path)).build()

    assert model.name == "focus-agent"
    assert model.purpose == "Finish interrupted software projects"
    assert model.core_flow == [
        "scan the repository",
        "create a task graph",
        "execute and verify tasks",
    ]
    assert model.non_goals == ["deploy automatically", "use secrets"]
    assert model.commands["test"] == "pytest"
    assert "focus (console script)" in model.entry_points
    assert model.tests == ["tests/test_flow.py"]
    assert set(model.sources) >= {
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/architecture.md",
        "pyproject.toml",
        "tests/test_flow.py",
    }
    assert set(model.needs_review) == {"purpose", "target_user", "core_flow", "non_goals"}


def test_write_creates_project_model_json(tmp_path):
    root = _project(tmp_path)

    model, path = ProjectModelBuilder(root).write()

    assert path == root / ".altai" / "project-model.json"
    assert json.loads(path.read_text(encoding="utf-8")) == model.to_dict()
    assert load_model(root) == model


def test_rebuild_preserves_agent_authored_fields(tmp_path):
    root = _project(tmp_path)
    model, _ = ProjectModelBuilder(root).write()
    model.purpose = "Help ADHD developers finish interrupted projects"
    model.derived.remove("purpose")
    from altai.intelligence import save_model

    save_model(model)
    (root / "pyproject.toml").write_text(
        "[project]\nname='focus-agent'\ndescription='A changed derived description'\n",
        encoding="utf-8",
    )

    rebuilt, _ = ProjectModelBuilder(root).write()

    assert rebuilt.purpose == "Help ADHD developers finish interrupted projects"
    assert "purpose" not in rebuilt.derived
    assert "purpose" not in rebuilt.needs_review


def test_bootstrap_produces_project_model_on_first_scan(tmp_path):
    root = _project(tmp_path)

    bootstrap(root)

    model = load_model(root)
    assert model is not None
    assert model.name == "focus-agent"
