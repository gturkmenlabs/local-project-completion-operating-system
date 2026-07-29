from __future__ import annotations

import hashlib
import io
import re
import tokenize
from pathlib import Path

from .models import ProjectState, Task

STACK_MARKERS = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "setup.py": "Python",
    "package.json": "Node.js",
    "tsconfig.json": "TypeScript",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "Dockerfile": "Docker",
}

TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b[:\s-]*(.*)", re.IGNORECASE)

#: A marker only counts when it is actually an annotation. Anything else is
#: prose or a string literal that merely contains the word.
COMMENT_STARTERS = ("//", "#", "/*", "*", "--", "<!--", ";", "%")
#: In markdown, only a line that *begins* with the marker (optionally behind a
#: bullet, heading or quote) is a real task.
MARKDOWN_LEAD = " \t>*-+#0123456789.)[]"

#: Directories never worth scanning for unfinished work.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".altai",
        ".claude",
        ".codex",
        ".agents",
        ".autoresearch",
        ".cursor",
        ".idea",
        ".vscode",
        "node_modules",
        "bower_components",
        "vendor",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "coverage",
        "htmlcov",
        "site-packages",
    }
)

SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".sh",
        ".md",
    }
)

#: Skip anything larger than this; generated/minified files produce only noise.
MAX_FILE_BYTES = 512_000
MAX_DISCOVERED_TASKS = 50

BASELINE_TASK_ID = "baseline-verification"
#: Risks re-derived on every scan are tagged so a stale one can be dropped when
#: the underlying condition clears.
SCAN_RISK_PREFIX = "[scan]"


def task_id_for_marker(relative_path: str, detail: str) -> str:
    """Stable ID derived from *content*, not scan order.

    Positional IDs (``todo-1``, ``todo-2``) silently remap to different work as
    soon as one marker is resolved, which corrupts any recorded progress.
    """
    digest = hashlib.sha1(f"{relative_path}::{detail.lower()}".encode("utf-8")).hexdigest()
    return f"todo-{digest[:10]}"


def detect_stack(root: Path, depth: int = 2) -> list[str]:
    """Look for stack markers at the root *and* in nearby subdirectories.

    Monorepo-ish layouts (``web/package.json``, ``backend/pyproject.toml``) are
    common; probing only the root reports "belirsiz" for an obvious Node project.
    """
    found: list[str] = []
    frontier = [(root, 0)]
    while frontier:
        current, level = frontier.pop(0)
        for marker, stack in STACK_MARKERS.items():
            if (current / marker).exists() and stack not in found:
                found.append(stack)
        if level >= depth:
            continue
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            continue
        for child in children:
            if child.name not in IGNORED_DIRS and not child.name.startswith("."):
                frontier.append((child, level + 1))
    return found


def scan_project(root: Path) -> ProjectState:
    root = root.resolve()
    state = ProjectState(root=root, name=root.name)
    state.stack.extend(detect_stack(root))

    readme = next(
        (p for p in (root / "README.md", root / "README.rst", root / "readme.md") if p.exists()),
        None,
    )
    if readme:
        lines = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
        state.goals.extend(_extract_goals(lines))

    discovered = _extract_todos(root)
    state.tasks.extend(discovered)
    if len(discovered) >= MAX_DISCOVERED_TASKS:
        # Silent truncation would let the project report "done" with unscanned
        # work left over. Surface it as a risk instead.
        state.risks.append(
            f"{SCAN_RISK_PREFIX} marker scan hit the {MAX_DISCOVERED_TASKS}-task cap; more "
            "TODO/FIXME work exists than is tracked. Re-run `start` after clearing some markers."
        )
    if not state.goals:
        state.goals.append(
            "Make the current project build, test, and satisfy its documented purpose."
        )
    if not state.tasks:
        state.tasks.append(
            Task(
                id=BASELINE_TASK_ID,
                title="Verify project baseline",
                description=(
                    "Detect build, test, lint and runtime commands; execute them and record failures."
                ),
                acceptance=[
                    "Build command identified",
                    "Tests executed",
                    "Failures recorded or baseline passes",
                ],
                discovered=True,
            )
        )
    return state


def _extract_goals(lines: list[str]) -> list[str]:
    goals: list[str] = []
    for line in lines[:120]:
        clean = line.strip(" #*-\t")
        if not clean:
            continue
        low = clean.lower()
        if any(key in low for key in ("goal", "purpose", "amaç", "hedef", "what it does")):
            goals.append(clean[:300])
        if len(goals) >= 5:
            break
    return goals


def _iter_source_files(root: Path):
    """Walk the tree, pruning ignored directories instead of filtering after the fact."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in IGNORED_DIRS:
                        stack.append(entry)
                    continue
                if entry.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                if entry.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield entry


def is_real_marker(line: str, match: re.Match, is_markdown: bool) -> bool:
    """Reject markers that are prose or string literals rather than annotations.

    Documentation and skill reference files mention "TODO" constantly; treating
    every occurrence as work produced tasks titled ``';`` from a real repo.
    """
    prefix = line[: match.start()]
    stripped = prefix.strip()
    if is_markdown:
        return not stripped or all(char in MARKDOWN_LEAD for char in stripped)
    if not stripped:
        return True
    for starter in COMMENT_STARTERS:
        index = prefix.rfind(starter)
        if index >= 0 and not prefix[index + len(starter) :].strip():
            return True
    return False


def _clean_detail(detail: str) -> str:
    return detail.strip().strip("`\"'*_ \t-").strip()


def _python_comments(content: str) -> list[tuple[int, str]]:
    comments: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                comments.append((token.start[0], token.string))
    except (IndentationError, SyntaxError, tokenize.TokenError):
        # Keep comments tokenized before an incomplete trailing statement. This
        # repository scanner often runs while source files are mid-edit.
        pass
    return comments


def _extract_todos(root: Path) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    for path in sorted(_iter_source_files(root)):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        is_markdown = path.suffix.lower() == ".md"
        if path.suffix.lower() == ".py":
            candidates = _python_comments(content)
        else:
            candidates = enumerate(content.splitlines(), 1)
        for number, line in candidates:
            match = TODO_RE.search(line)
            if not match or not is_real_marker(line, match, is_markdown):
                continue
            detail = _clean_detail(match.group(2)) or "Resolve marked unfinished work"
            task_id = task_id_for_marker(relative, detail)
            if task_id in seen:
                continue
            seen.add(task_id)
            tasks.append(
                Task(
                    id=task_id,
                    title=detail[:100],
                    description=f"{relative}:{number}",
                    acceptance=[
                        "Marked work implemented",
                        "Relevant test passes",
                        "TODO marker removed or justified",
                    ],
                    discovered=True,
                )
            )
            if len(tasks) >= MAX_DISCOVERED_TASKS:
                return tasks
    return tasks
