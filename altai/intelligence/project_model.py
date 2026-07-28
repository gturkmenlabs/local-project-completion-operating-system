"""Derive `.altai/project-model.json`: the project's declared purpose.

The task graph knows what is *unfinished*. It does not know what the project is
*for*, so every task it hands out is judged against "does this marker still
exist" instead of "does this serve the product". This module supplies the
missing half.

It follows the same rule as :mod:`altai.research`: ALTAI does not interpret on
its own. It extracts what the repository actually *declares* — manifests, README
prose, entry points, documented commands — and marks the genuinely interpretive
fields as unconfirmed. The host agent (Claude Code / Codex) confirms or rewrites
them; the builder then leaves them alone forever.

That last guarantee is what ``derived`` records: the field names this builder
produced. A field that is non-empty and *not* listed there was written by a
human or the host agent and is never overwritten by a later build — the same
manual-versus-automatic split ``Task.blocked_auto`` uses for blocks.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # tomllib is 3.11+; this package supports 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    tomllib = None  # type: ignore[assignment]

from ..memory import atomic_write_text, init_workspace, workspace_path
from ..scanner import IGNORED_DIRS, detect_stack

MODEL_FILENAME = "project-model.json"
MODEL_SCHEMA_VERSION = 1

#: Fields carried across rebuilds. ``root`` is positional context and the three
#: bookkeeping lists are recomputed every time, so none of them belong here.
MERGEABLE_FIELDS = (
    "name",
    "purpose",
    "target_user",
    "stack",
    "entry_points",
    "commands",
    "core_flow",
    "non_goals",
    "docs",
)

#: Fields no file can state outright. A derived value here is a *candidate*, and
#: stays in ``needs_review`` until the host agent confirms it.
INTERPRETIVE_FIELDS = ("purpose", "target_user", "core_flow", "non_goals")

#: Read caps. A vendored 3 MB README is noise, and so is its 400th bullet.
MAX_DOC_BYTES = 200_000
MAX_PURPOSE_CHARS = 400
MAX_LIST_ITEMS = 12
MAX_ITEM_CHARS = 200
MAX_HISTORY_ITEMS = 20

DOC_CANDIDATES = ("README.md", "README.rst", "readme.md", "AGENTS.md", "CLAUDE.md")
#: Purpose is read from product documentation only. AGENTS.md/CLAUDE.md describe
#: how an agent should behave, which is not what the project is for.
PURPOSE_DOCS = ("README.md", "README.rst", "readme.md")
BACKLOG_CANDIDATES = (
    "BACKLOG.md",
    "TODO.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "docs/backlog.md",
    "docs/roadmap.md",
)
TEST_DIRS = ("tests", "test", "spec", "__tests__")

ENTRY_CANDIDATES = (
    "main.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "index.js",
    "index.ts",
    "server.js",
    "main.go",
    "src/main.py",
    "src/index.js",
    "src/index.ts",
    "src/main.ts",
    "src/main.rs",
    "src/App.tsx",
    "cmd/main.go",
)

#: Script names worth surfacing as project commands, in reporting order.
INTERESTING_SCRIPTS = ("test", "build", "lint", "typecheck", "start", "dev")
MAKE_TARGETS = ("test", "build", "lint", "run", "dev", "check")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)")

#: Headings that introduce explicit non-goals, in English and Turkish. Turkish
#: is folded to ASCII before matching (see ``_fold``).
NON_GOAL_HEADINGS = (
    "non-goal",
    "non goal",
    "not a goal",
    "out of scope",
    "will not",
    "kapsam disi",
    "hedef degil",
    "yapmaz",
)
#: Headings that introduce the main flow the project performs.
FLOW_HEADINGS = (
    "how it works",
    "workflow",
    "work flow",
    "the loop",
    "loop",
    "flow",
    "pipeline",
    "usage",
    "nasil calisir",
    "akis",
    "dongu",
    "kullanim",
)

#: Turkish letters have no ASCII decomposition and ``str.lower`` mangles the
#: dotted/dotless I pair, so fold explicitly before matching keywords.
_FOLD = str.maketrans(
    {
        "ı": "i", "I": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "â": "a", "î": "i", "û": "u",
    }
)


def _fold(text: str) -> str:
    return text.translate(_FOLD).lower()


@dataclass(slots=True)
class ProjectModel:
    """What the repository says it is. Persisted to ``.altai/project-model.json``."""

    root: Path
    name: str
    purpose: str = ""
    target_user: str = ""
    stack: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    #: label -> shell command, e.g. ``{"test": "pytest"}``.
    commands: dict[str, str] = field(default_factory=dict)
    core_flow: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    backlog: list[str] = field(default_factory=list)
    recent_history: list[str] = field(default_factory=list)
    #: Files this build actually read, so a wrong answer can be traced.
    sources: list[str] = field(default_factory=list)
    #: Field names the builder produced; everything else non-empty was authored.
    derived: list[str] = field(default_factory=list)
    #: Interpretive fields still lacking a confirmed answer.
    needs_review: list[str] = field(default_factory=list)
    schema_version: int = MODEL_SCHEMA_VERSION

    @property
    def confirmed(self) -> bool:
        """True once every interpretive field has an agent-authored answer."""
        return not self.needs_review

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["root"] = str(self.root)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], root: Path | None = None) -> "ProjectModel":
        """Tolerant loader. This file is meant to be edited by hand, so a typo
        must produce a clean error rather than an AttributeError deep in a call
        stack — the same contract :meth:`Task.from_dict` honours."""
        if not isinstance(data, dict):
            raise ValueError(f"Project model must be an object, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}

        for name in (
            "stack", "entry_points", "core_flow", "non_goals", "docs", "tests", "backlog",
            "recent_history", "sources", "derived", "needs_review",
        ):
            value = payload.get(name)
            if value is None:
                continue
            if not isinstance(value, list):
                raise ValueError(f"Project model field '{name}' must be a list")
            payload[name] = [str(item) for item in value]

        commands = payload.get("commands")
        if commands is not None:
            if not isinstance(commands, dict):
                raise ValueError("Project model field 'commands' must be an object")
            payload["commands"] = {str(key): str(value) for key, value in commands.items()}

        for name in ("name", "purpose", "target_user"):
            if name in payload and payload[name] is not None:
                payload[name] = str(payload[name])

        try:
            payload["schema_version"] = int(payload.get("schema_version", MODEL_SCHEMA_VERSION))
        except (TypeError, ValueError):
            payload["schema_version"] = MODEL_SCHEMA_VERSION

        payload["root"] = Path(root if root is not None else payload.get("root", "."))
        payload.setdefault("name", "")
        return cls(**payload)

    def as_text(self) -> str:
        lines = [
            f"Proje: {self.name}",
            f"Amac: {self.purpose or '(bilinmiyor)'}",
            f"Kullanici: {self.target_user or '(bilinmiyor)'}",
            f"Stack: {', '.join(self.stack) or 'belirsiz'}",
        ]
        if self.commands:
            lines.append("Komut: " + ", ".join(f"{k}={v}" for k, v in self.commands.items()))
        if self.entry_points:
            lines.append("Giris: " + ", ".join(self.entry_points[:5]))
        for step in self.core_flow[:6]:
            lines.append(f"Akis: {step}")
        for item in self.non_goals[:4]:
            lines.append(f"Kapsam disi: {item}")
        if self.needs_review:
            lines.append("Dogrulanacak: " + ", ".join(self.needs_review))
        else:
            lines.append("Model: onaylandi")
        return "\n".join(lines)


def model_path(root: Path) -> Path:
    return workspace_path(root) / MODEL_FILENAME


def save_model(model: ProjectModel) -> Path:
    init_workspace(model.root)
    model.schema_version = MODEL_SCHEMA_VERSION
    payload = json.dumps(model.to_dict(), ensure_ascii=False, indent=2)
    return atomic_write_text(model_path(model.root), payload, prefix=".model-")


def load_model(root: Path) -> ProjectModel | None:
    """Return the persisted model, or ``None`` when there is nothing usable.

    A corrupt file is treated as absent rather than fatal: the model is an aid,
    and losing it must never stop the agent from working through the graph.
    """
    path = model_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ProjectModel.from_dict(data, root=Path(root).resolve())
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_DOC_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _clean(text: str) -> str:
    """Strip markdown decoration but keep identifiers intact.

    Underscores survive on purpose: ``project_state.json`` must not become
    ``projectstate.json`` in a purpose sentence.
    """
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_RE.sub("", text)
    return text.replace("`", "").replace("*", "").strip()


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split markdown into ``(folded heading, body lines)`` pairs.

    Fenced code is dropped: a usage example is not prose, and its ``#`` comments
    would otherwise register as headings.
    """
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            sections.append((_fold(_clean(heading.group(2))), []))
            continue
        sections[-1][1].append(line)
    return sections


def _first_paragraph(lines: list[str]) -> str:
    buffer: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("!", "[!", "<", "|", ">")):
            # Blank line ends the paragraph; badges and tables never start one.
            if buffer:
                break
            continue
        if _LIST_RE.match(stripped):
            if buffer:
                break
            continue
        buffer.append(stripped)
    return _clean(" ".join(buffer))[:MAX_PURPOSE_CHARS].strip()


def _list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        match = _LIST_RE.match(line)
        if not match:
            continue
        item = _clean(match.group(1))[:MAX_ITEM_CHARS].strip()
        if item and item not in items:
            items.append(item)
        if len(items) >= MAX_LIST_ITEMS:
            break
    return items


def _section_items(sections: list[tuple[str, list[str]]], keywords: tuple[str, ...]) -> list[str]:
    for heading, body in sections:
        if heading and any(key in heading for key in keywords):
            items = _list_items(body)
            if items:
                return items
    return []


def _naive_toml(text: str) -> dict[str, Any]:
    """Minimal fallback for Python 3.10, which has no ``tomllib``.

    Recovers string scalars under dotted section headers — enough for
    ``[project] name/description`` and ``[project.scripts]``. Anything richer
    (arrays, inline tables) is deliberately ignored rather than guessed at.
    """
    data: dict[str, Any] = {}
    section: dict[str, Any] = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data
            for part in line[1:-1].strip().lstrip("[").rstrip("]").split("."):
                key = part.strip().strip("\"'")
                nested = section.get(key)
                if not isinstance(nested, dict):
                    nested = {}
                    section[key] = nested
                section = nested
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip().split("#")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            section[key.strip().strip("\"'")] = value[1:-1]
    return data


def _load_toml(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if not text:
        return {}
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            return {}
    return _naive_toml(text)


def _load_json(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class ProjectModelBuilder:
    """Build a :class:`ProjectModel` from what the repository declares.

    Usage::

        model, path = ProjectModelBuilder(root).write()

    :meth:`write` merges over any existing model, so agent-authored answers
    survive every rescan.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    # -- public API ---------------------------------------------------------

    def build(self) -> ProjectModel:
        """Derive a model from the current file tree. Nothing is read back."""
        model = ProjectModel(root=self.root, name=self.root.name)
        model.stack = detect_stack(self.root)
        self._read_manifests(model)
        self._read_docs(model)
        self._read_repository_context(model)
        self._find_entry_points(model)
        self._find_make_targets(model)
        # Every non-empty field of a fresh build is, by definition, derived.
        model.derived = [name for name in MERGEABLE_FIELDS if _has_value(model, name)]
        _mark_review(model)
        return model

    def write(self, previous: ProjectModel | None = None) -> tuple[ProjectModel, Path]:
        """Build, merge over the stored model, and persist it."""
        if previous is None:
            previous = load_model(self.root)
        model = merge_model(previous, self.build())
        return model, save_model(model)

    # -- extraction ---------------------------------------------------------

    def _record(self, model: ProjectModel, path: Path) -> None:
        relative = path.relative_to(self.root).as_posix()
        if relative not in model.sources:
            model.sources.append(relative)

    def _read_manifests(self, model: ProjectModel) -> None:
        pyproject = self.root / "pyproject.toml"
        data = _load_toml(pyproject)
        project = data.get("project")
        if isinstance(project, dict):
            self._record(model, pyproject)
            model.name = str(project.get("name") or model.name)
            description = project.get("description")
            if isinstance(description, str) and description.strip():
                model.purpose = _clean(description)[:MAX_PURPOSE_CHARS]
            scripts = project.get("scripts")
            if isinstance(scripts, dict):
                for name in scripts:
                    _append(model.entry_points, f"{name} (console script)")
        tool = data.get("tool")
        if isinstance(tool, dict) and "pytest" in tool:
            model.commands.setdefault("test", "pytest")

        package = self.root / "package.json"
        data = _load_json(package)
        if data:
            self._record(model, package)
            if isinstance(data.get("name"), str) and data["name"].strip():
                model.name = data["name"]
            description = data.get("description")
            if not model.purpose and isinstance(description, str) and description.strip():
                model.purpose = _clean(description)[:MAX_PURPOSE_CHARS]
            main = data.get("main")
            if isinstance(main, str) and main.strip():
                _append(model.entry_points, main)
            scripts = data.get("scripts")
            if isinstance(scripts, dict):
                for name in INTERESTING_SCRIPTS:
                    if isinstance(scripts.get(name), str):
                        model.commands.setdefault(
                            name, "npm test" if name == "test" else f"npm run {name}"
                        )

    def _read_docs(self, model: ProjectModel) -> None:
        seen: set[tuple[int, int]] = set()
        for filename in DOC_CANDIDATES:
            path = self.root / filename
            text = _read_text(path)
            if not text:
                continue
            try:
                identity = (path.stat().st_dev, path.stat().st_ino)
            except OSError:
                continue
            if identity in seen:
                continue
            seen.add(identity)
            model.docs.append(filename)
            self._record(model, path)
            sections = _sections(text)
            if filename in PURPOSE_DOCS:
                if not model.purpose:
                    model.purpose = self._purpose_from(sections)
                if not model.non_goals:
                    model.non_goals = _section_items(sections, NON_GOAL_HEADINGS)
                if not model.core_flow:
                    model.core_flow = _section_items(sections, FLOW_HEADINGS)

        docs_dir = self.root / "docs"
        if docs_dir.is_dir():
            for path in sorted(docs_dir.rglob("*.md"))[:MAX_LIST_ITEMS]:
                if any(part in IGNORED_DIRS for part in path.parts):
                    continue
                if not _read_text(path):
                    continue
                relative = path.relative_to(self.root).as_posix()
                model.docs.append(relative)
                self._record(model, path)

    def _read_repository_context(self, model: ProjectModel) -> None:
        for relative in BACKLOG_CANDIDATES:
            path = self.root / relative
            if not _read_text(path):
                continue
            model.backlog.append(relative)
            self._record(model, path)

        for dirname in TEST_DIRS:
            test_dir = self.root / dirname
            if not test_dir.is_dir():
                continue
            for path in sorted(test_dir.rglob("*")):
                if len(model.tests) >= MAX_LIST_ITEMS:
                    break
                if not path.is_file() or path.suffix.lower() not in {
                    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".cs",
                }:
                    continue
                if not _read_text(path):
                    continue
                relative = path.relative_to(self.root).as_posix()
                model.tests.append(relative)
                self._record(model, path)

        try:
            result = subprocess.run(
                ["git", "log", f"-{MAX_HISTORY_ITEMS}", "--pretty=format:%s"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if result.returncode == 0:
            model.recent_history = [
                line.strip()[:MAX_ITEM_CHARS]
                for line in result.stdout.splitlines()
                if line.strip()
            ]

    def _purpose_from(self, sections: list[tuple[str, list[str]]]) -> str:
        for _, body in sections:
            paragraph = _first_paragraph(body)
            if len(paragraph) >= 20:  # a one-word line is a caption, not a purpose
                return paragraph
        return ""

    def _find_entry_points(self, model: ProjectModel) -> None:
        for relative in ENTRY_CANDIDATES:
            if (self.root / relative).is_file():
                _append(model.entry_points, relative)
        try:
            children = sorted(p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for child in children:
            if child.name in IGNORED_DIRS or child.name.startswith("."):
                continue
            if (child / "__main__.py").is_file():
                _append(model.entry_points, f"{child.name}/__main__.py")

    def _find_make_targets(self, model: ProjectModel) -> None:
        makefile = next(
            (p for p in (self.root / "Makefile", self.root / "makefile") if p.is_file()), None
        )
        if makefile is None:
            return
        text = _read_text(makefile)
        if not text:
            return
        self._record(model, makefile)
        for line in text.splitlines():
            match = _MAKE_TARGET_RE.match(line)
            if match and match.group(1) in MAKE_TARGETS:
                model.commands.setdefault(match.group(1), f"make {match.group(1)}")


def _append(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _has_value(model: ProjectModel, name: str) -> bool:
    return bool(getattr(model, name))


def _mark_review(model: ProjectModel) -> None:
    """An interpretive field needs review while it is empty or merely derived."""
    model.needs_review = [
        name
        for name in INTERPRETIVE_FIELDS
        if not _has_value(model, name) or name in model.derived
    ]


def merge_model(previous: ProjectModel | None, fresh: ProjectModel) -> ProjectModel:
    """Fold a fresh build into a stored model.

    An *authored* field — non-empty and absent from ``previous.derived`` — always
    wins: the agent's judgement about the project's purpose outranks a paragraph
    scraped from a README, and re-deriving it on every scan would erase the one
    thing this file exists to hold.

    A builder-owned field takes the fresh value even when that value is empty.
    The model must mirror the repository: a purpose whose source paragraph was
    deleted is exactly the drift the agent needs to see, not something to paper
    over with a stale candidate.
    """
    if previous is None:
        _mark_review(fresh)
        return fresh

    authored = [
        name
        for name in MERGEABLE_FIELDS
        if _has_value(previous, name) and name not in previous.derived
    ]
    for name in authored:
        value = getattr(previous, name)
        setattr(fresh, name, dict(value) if isinstance(value, dict) else _copy(value))
    fresh.derived = [name for name in fresh.derived if name not in authored]
    _mark_review(fresh)
    return fresh


def _copy(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value
