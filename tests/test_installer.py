import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from install_into_project import BEGIN, END, install, main  # noqa: E402

from altai import __version__  # noqa: E402


def test_install_does_not_pollute_project_root(tmp_path):
    install(tmp_path)
    top_level = {entry.name for entry in tmp_path.iterdir()}
    assert "altai" not in top_level, "the package must not land in the project root"
    assert (tmp_path / ".altai" / "tool" / "altai" / "cli.py").exists()
    assert (tmp_path / ".altai" / "tool" / "altai" / "intelligence" / "project_model.py").exists()
    assert (tmp_path / ".altai" / "tool" / "altai" / "design" / "product_architect.py").exists()
    assert (tmp_path / ".codex" / "skills" / "caveman" / "SKILL.md").exists()
    assert (tmp_path / ".altai" / "tool" / "run.py").exists()
    manifest = json.loads((tmp_path / ".altai" / "integration.json").read_text(encoding="utf-8"))
    # Read the version rather than pinning it here: a hardcoded copy turns every
    # release into a failing test that says nothing about the installer.
    assert manifest["altai_version"] == __version__
    assert manifest["features"] == ["altai", "product-design", "caveman"]
    # The installed project's advertised entry point is the single command.
    assert manifest["commands"]["run"].endswith("run .")
    assert manifest["commands"]["continue"] == manifest["commands"]["run"]
    assert manifest["commands"]["design"].endswith("run . --design")
    assert manifest["commands"]["safe"].endswith("run . --safe")


def test_launcher_runs_from_project_root(tmp_path):
    (tmp_path / "app.py").write_text("# TODO: something\n", encoding="utf-8")
    install(tmp_path)
    result = subprocess.run(
        [sys.executable, ".altai/tool/run.py", "start", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Proje:" in result.stdout
    assert (tmp_path / ".altai" / "project-state.json").exists()


def test_existing_claude_md_is_preserved(tmp_path):
    original = "# My project rules\n\nAlways run make lint.\n"
    (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
    install(tmp_path)
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Always run make lint." in content
    assert BEGIN in content and END in content


def test_reinstall_does_not_duplicate_the_block(tmp_path):
    install(tmp_path)
    install(tmp_path)
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert content.count(BEGIN) == 1
    assert content.count(END) == 1


def test_pycache_is_not_vendored(tmp_path):
    install(tmp_path)
    assert not list((tmp_path / ".altai" / "tool").rglob("__pycache__"))


def test_install_can_exclude_caveman(tmp_path):
    install(tmp_path, include_caveman=False)

    assert not (tmp_path / ".codex" / "skills" / "caveman").exists()
    manifest = json.loads((tmp_path / ".altai" / "integration.json").read_text(encoding="utf-8"))
    assert manifest["features"] == ["altai", "product-design"]


def test_dry_run_writes_nothing(tmp_path):
    target = tmp_path / "new-project"

    actions = install(target, dry_run=True)

    assert any("would install" in action for action in actions)
    assert not target.exists()


def test_main_installs_multiple_projects(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_into_project.py", str(first), str(second)],
    )

    assert main() == 0
    assert (first / ".altai" / "integration.json").exists()
    assert (second / ".altai" / "integration.json").exists()
