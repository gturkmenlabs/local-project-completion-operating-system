"""Build a file -> symbol -> call graph and persist it to `.altai/code-graph.json`.

:mod:`.project_model` answers "what is this project for" and :mod:`.gap_analyzer`
answers "where does that answer contradict the repository". Neither one tells the
host agent *where* to look once it has a task — that still means grepping the
whole tree from scratch on every task. This module answers that third question:
given a task's title, which files most likely need to change.

Python source is parsed with :mod:`ast`, so classes, functions and (best-effort,
unresolved) call names are exact. Every other language gets a regex pass over
top-level declarations only — a name, not a call graph — because parsing them
correctly needs a real compiler front-end and this package ships with zero
dependencies on purpose (see ``pyproject.toml``). A symbol from a regex pass is
still useful for the one thing this module is for — matching a task's words
against a file — even though it cannot tell you what that symbol calls.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text, init_workspace, workspace_path
from ..scanner import IGNORED_DIRS

GRAPH_FILENAME = "code-graph.json"
GRAPH_SCHEMA_VERSION = 1

#: Caps so a vendored dependency or a generated file cannot blow up build time.
MAX_FILES = 400
MAX_FILE_BYTES = 400_000
MAX_SYMBOLS = 4000
#: Ceiling on paths collected before MAX_FILES is even applied — see
#: build_code_graph's docstring.
MAX_CANDIDATE_PATHS = MAX_FILES * 20
#: Skip entries recorded by name; see _record_skip.
MAX_SKIPPED_RECORDED = 50

#: Suffix -> language, and whether :mod:`ast` applies. Only Python gets call edges.
PYTHON_SUFFIXES = frozenset({".py"})
#: name-declaring keyword per non-Python language, used by the regex pass.
REGEX_LANGUAGES: dict[str, tuple[str, ...]] = {
    ".js": ("function", "class"),
    ".jsx": ("function", "class"),
    ".mjs": ("function", "class"),
    ".ts": ("function", "class", "interface"),
    ".tsx": ("function", "class", "interface"),
    ".go": ("func", "type"),
    ".rs": ("fn", "struct", "enum", "trait"),
    ".java": ("class", "interface"),
    ".kt": ("class", "fun", "interface"),
    ".rb": ("class", "def", "module"),
    ".php": ("class", "function"),
    ".cs": ("class", "interface"),
}
SOURCE_SUFFIXES = PYTHON_SUFFIXES | frozenset(REGEX_LANGUAGES)

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _regex_for(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?{keyword}\s+\**({_NAME_RE.pattern})")


@dataclass(slots=True)
class Symbol:
    """One declaration: a class, function or method."""

    name: str
    kind: str
    file: str
    line: int
    #: Last line of the declaration. Python only (``ast`` gives it for free);
    #: 0 for regex-derived languages, where there is no reliable end marker.
    end_line: int = 0
    #: Unresolved callee names found in the body. Python only; always empty
    #: for the regex-derived languages.
    calls: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Line count of the declaration, or 0 when ``end_line`` is unknown."""
        return max(0, self.end_line - self.line + 1) if self.end_line else 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Symbol":
        known = {f.name for f in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        payload.setdefault("name", "")
        payload.setdefault("kind", "")
        payload.setdefault("file", "")
        payload.setdefault("line", 0)
        payload.setdefault("end_line", 0)
        payload["line"] = int(payload.get("line") or 0)
        payload["end_line"] = int(payload.get("end_line") or 0)
        payload["calls"] = [str(c) for c in payload.get("calls") or []]
        return cls(**payload)


@dataclass(slots=True)
class CodeGraph:
    """Every file scanned and every symbol found in it."""

    root: Path
    files: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    #: Files that exist but were skipped (too large, unreadable, over the cap).
    skipped: list[str] = field(default_factory=list)
    #: True when a cap (MAX_CANDIDATE_PATHS, MAX_FILES or MAX_SYMBOLS) was hit,
    #: meaning source files exist that this scan never even looked at. Checked
    #: explicitly rather than left implicit in ``skipped`` because ``skipped``
    #: itself is capped (see MAX_SKIPPED_RECORDED) and, separately, a file
    #: dropped before the candidate cap is applied never reaches ``skipped``
    #: at all — silence there must not read as completeness.
    truncated: bool = False
    schema_version: int = GRAPH_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "files": self.files,
            "symbols": [s.to_dict() for s in self.symbols],
            "skipped": self.skipped,
            "truncated": self.truncated,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], root: Path | None = None) -> "CodeGraph":
        if not isinstance(data, dict):
            raise ValueError(f"Code graph must be an object, got {type(data).__name__}")
        symbols_data = data.get("symbols", [])
        if not isinstance(symbols_data, list):
            raise ValueError("Code graph field 'symbols' must be a list")
        files = data.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Code graph field 'files' must be a list")
        skipped = data.get("skipped", [])
        if not isinstance(skipped, list):
            raise ValueError("Code graph field 'skipped' must be a list")
        try:
            schema_version = int(data.get("schema_version", GRAPH_SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema_version = GRAPH_SCHEMA_VERSION
        return cls(
            root=Path(root if root is not None else data.get("root", ".")),
            files=[str(f) for f in files],
            symbols=[Symbol.from_dict(item) for item in symbols_data],
            skipped=[str(s) for s in skipped],
            truncated=bool(data.get("truncated", False)),
            schema_version=schema_version,
        )

    def symbols_in(self, relative_path: str) -> list[Symbol]:
        return [s for s in self.symbols if s.file == relative_path]

    def callers_of(self, name: str) -> list[Symbol]:
        return [s for s in self.symbols if name in s.calls]


def graph_path(root: Path) -> Path:
    return workspace_path(root) / GRAPH_FILENAME


def save_graph(graph: CodeGraph) -> Path:
    init_workspace(graph.root)
    graph.schema_version = GRAPH_SCHEMA_VERSION
    payload = json.dumps(graph.to_dict(), ensure_ascii=False, indent=2)
    return atomic_write_text(graph_path(graph.root), payload, prefix=".graph-")


def load_graph(root: Path) -> CodeGraph | None:
    path = graph_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CodeGraph.from_dict(data, root=Path(root).resolve())
    except ValueError:
        return None


def _iter_source_files(root: Path):
    """Breadth-first, siblings sorted at each level.

    A depth-first walk with an unsorted stack can fully exhaust a single
    early-encountered subtree — a large, merely-forgotten-to-ignore generated
    or vendor directory — before ever visiting a sibling. Combined with
    MAX_CANDIDATE_PATHS that meant real first-party source could be dropped
    silently. Breadth-first, sorted order means every directory at a given
    depth is at least discovered before the walk goes one level deeper into
    any of them, so the collection cap in :func:`build_code_graph` runs out
    across breadth rather than being consumed entirely by one subtree.
    """
    level = [root]
    while level:
        next_level: list[Path] = []
        for current in level:
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if entry.name not in IGNORED_DIRS:
                            next_level.append(entry)
                        continue
                    if entry.suffix.lower() not in SOURCE_SUFFIXES:
                        continue
                except OSError:
                    continue
                yield entry
        level = next_level


class _CallCollector(ast.NodeVisitor):
    """Collect unresolved callee names reachable from one function body.

    Does not recurse into nested function/class definitions — their calls
    belong to *that* symbol, not the enclosing one.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor naming
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name and name not in self.calls:
            self.calls.append(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        pass  # do not descend into a nested function's own body

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        pass


def _calls_in(node: ast.AST) -> list[str]:
    collector = _CallCollector()
    for child in ast.iter_child_nodes(node):
        collector.visit(child)
    return collector.calls


def _python_symbols(relative: str, source: str) -> list[Symbol] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    symbols: list[Symbol] = []

    def walk(node: ast.AST, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind="class",
                        file=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", 0) or 0,
                    )
                )
                walk(child, in_class=True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if in_class else "function"
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind=kind,
                        file=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", 0) or 0,
                        calls=_calls_in(child),
                    )
                )
                walk(child, in_class=False)
            else:
                walk(child, in_class=in_class)

    walk(tree, in_class=False)
    return symbols


def _regex_symbols(relative: str, source: str, suffix: str) -> list[Symbol]:
    keywords = REGEX_LANGUAGES.get(suffix, ())
    if not keywords:
        return []
    patterns = [(keyword, _regex_for(keyword)) for keyword in keywords]
    symbols: list[Symbol] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        for keyword, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            kind = "class" if keyword in ("class", "type", "struct", "interface", "trait", "enum") else "function"
            symbols.append(Symbol(name=match.group(1), kind=kind, file=relative, line=lineno))
            break
    return symbols


def _record_skip(graph: CodeGraph, relative: str) -> None:
    """Cap how many skip entries are kept by name.

    Past the cap, files are still (cheaply) skipped — only the *record* of
    having skipped them stops growing. Without this, a tree with far more
    matching files than ``MAX_FILES`` (a vendor directory that slipped past
    ``IGNORED_DIRS``, say) writes an unbounded ``skipped`` list into
    ``.altai/code-graph.json`` even though the graph itself stayed capped.
    """
    if len(graph.skipped) < MAX_SKIPPED_RECORDED:
        graph.skipped.append(relative)


def build_code_graph(root: Path) -> CodeGraph:
    """Walk the tree once and derive every symbol it can find.

    Read-only and best-effort throughout: a file that fails to parse is
    recorded in ``skipped`` rather than aborting the whole scan, the same
    tolerance :func:`altai.scanner.scan_project` extends to unreadable files.

    Candidate paths are collected only up to ``MAX_CANDIDATE_PATHS`` *before*
    sorting or reading anything — ``MAX_FILES`` alone only bounds how many
    files get parsed, not how many paths the walk collects into memory first,
    so an under-ignored tree with far more matching files than that could
    still make the walk itself unbounded.
    """
    root = Path(root).resolve()
    graph = CodeGraph(root=root)

    candidates: list[Path] = []
    for path in _iter_source_files(root):
        candidates.append(path)
        if len(candidates) >= MAX_CANDIDATE_PATHS:
            # Source files exist beyond this point that the walk never even
            # reached — distinct from (and can't be recorded in) `skipped`,
            # which only covers files the walk got as far as looking at.
            graph.truncated = True
            break
    candidates.sort()

    count = 0
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if count >= MAX_FILES or len(graph.symbols) >= MAX_SYMBOLS:
            graph.truncated = True
            _record_skip(graph, relative)
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                _record_skip(graph, relative)
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            _record_skip(graph, relative)
            continue

        suffix = path.suffix.lower()
        if suffix in PYTHON_SUFFIXES:
            found = _python_symbols(relative, source)
            if found is None:
                _record_skip(graph, relative)
                continue
        else:
            found = _regex_symbols(relative, source, suffix)

        graph.files.append(relative)
        graph.symbols.extend(found[: max(0, MAX_SYMBOLS - len(graph.symbols))])
        count += 1
    return graph


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
#: Words too common to narrow anything down. Matching on "add" or "the" would
#: surface half the repository for every task.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "add", "fix",
        "implement", "update", "resolve", "handle", "make", "use", "using",
        "not", "should", "does", "into", "over", "all", "todo", "fixme",
    }
)


def related_files(graph: CodeGraph, text: str, limit: int = 5) -> list[str]:
    """Rank files by how many of *text*'s significant words appear in them.

    Matches against symbol names and the file's own path segments. A task
    titled "fix login redirect" ranks ``auth/login.py`` (symbol ``login``)
    above a file that merely happens to be Python. Ties break on path so the
    result is stable across repeated calls.
    """
    words = {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS
    if not words:
        return []
    scores: dict[str, int] = {}
    for relative in graph.files:
        haystack = {part.lower() for part in re.split(r"[/_.\-]", relative) if part}
        score = len(words & haystack)
        if score:
            scores[relative] = scores.get(relative, 0) + score
    for symbol in graph.symbols:
        name_words = {w.lower() for w in re.findall(r"[A-Za-z][a-z0-9]*", symbol.name)}
        score = len(words & name_words)
        if score:
            scores[symbol.file] = scores.get(symbol.file, 0) + score
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [relative for relative, _ in ranked[:limit]]
