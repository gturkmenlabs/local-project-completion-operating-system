import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from install_into_project import BEGIN, END, install  # noqa: E402


def test_install_does_not_pollute_project_root(tmp_path):
    install(tmp_path)
    top_level = {entry.name for entry in tmp_path.iterdir()}
    assert "altai" not in top_level, "the package must not land in the project root"
    assert (tmp_path / ".altai" / "tool" / "altai" / "cli.py").exists()
    assert (tmp_path / ".altai" / "tool" / "altai" / "intelligence" / "project_model.py").exists()
    assert (tmp_path / ".altai" / "tool" / "run.py").exists()


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
