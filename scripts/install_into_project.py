"""Install ALTAI into an existing project without polluting its root.

The v0.1 installer copied the whole ``altai/`` Python package into the target
repository root. That collides with any project that already has an ``altai``
module, shows up in the user's diff, and only works when the CWD happens to be
the repo root. Here the package is vendored under ``.altai/tool/`` and reached
through a tiny launcher, so the visible footprint is:

    .altai/            (already git-ignorable state directory)
    .claude/skills/altai/, .claude/agents/altai-*.md
    .codex/skills/altai/
    AGENTS.md, CLAUDE.md   (created, or appended to if they already exist)
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]

BEGIN = "<!-- BEGIN ALTAI -->"
END = "<!-- END ALTAI -->"

LAUNCHER = '''"""Launcher so `python .altai/tool/run.py ...` works from the project root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from altai.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
'''


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".DS_Store", "*.egg-info"
        ),
    )


def _merge_markdown(source: Path, target: Path) -> str:
    """Create the file, or splice the ALTAI block into an existing one."""
    block = f"{BEGIN}\n{source.read_text(encoding='utf-8').strip()}\n{END}\n"
    if not target.exists():
        target.write_text(block, encoding="utf-8")
        return f"created {target.name}"

    current = target.read_text(encoding="utf-8")
    if BEGIN in current and END in current:
        head, _, rest = current.partition(BEGIN)
        _, _, tail = rest.partition(END)
        target.write_text(f"{head}{block}{tail.lstrip()}", encoding="utf-8")
        return f"updated ALTAI section in {target.name}"

    separator = "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
    target.write_text(f"{current}{separator}{block}", encoding="utf-8")
    return f"appended ALTAI section to {target.name}"


def install(target: Path) -> list[str]:
    target = target.resolve()
    if target == SOURCE_ROOT:
        raise SystemExit("Hedef, ALTAI kaynak klasorunun kendisi olamaz.")
    target.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []

    tool_dir = target / ".altai" / "tool"
    tool_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree(SOURCE_ROOT / "altai", tool_dir / "altai")
    (tool_dir / "run.py").write_text(LAUNCHER, encoding="utf-8")
    actions.append("vendored package -> .altai/tool/altai")

    for folder in (".claude", ".codex"):
        source = SOURCE_ROOT / folder
        if source.is_dir():
            _copy_tree(source, target / folder)
            actions.append(f"merged {folder}/")

    for name in ("AGENTS.md", "CLAUDE.md"):
        source = SOURCE_ROOT / name
        if source.is_file():
            actions.append(_merge_markdown(source, target / name))

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="ALTAI'yi bir projeye kur")
    parser.add_argument("target", help="Hedef proje klasoru")
    args = parser.parse_args()

    target = Path(args.target)
    for action in install(target):
        print(f"  - {action}")
    print(f"\nALTAI kuruldu: {target.resolve()}")
    print("Calistir:")
    print(f"  cd {target}")
    print("  python .altai/tool/run.py start .")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
