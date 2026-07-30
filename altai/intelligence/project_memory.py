"""Persistent, agent-authored memory under `.altai/memory/`.

Evidence (:func:`altai.memory.record_evidence`) answers "what happened on this
task". It says nothing about *why* a particular approach was chosen, what was
tried and abandoned, or a convention the host agent worked out the hard way —
and evidence is keyed by task ID, so none of that survives past the task it was
recorded on. This module is where it goes instead: five category files plus a
small rules list, all scoped to the *project*, not to any one task, and all
read again on the next task's brief.

Every write here is explicit. Nothing in this module infers a decision from a
diff or a commit message — the same restraint :mod:`.project_model` applies to
interpretive fields. A rule or a decision is only as good as the judgement that
produced it, so it is only ever written by a caller choosing to write it, through
:func:`record` or :func:`record_rule` (wired to ``altai learn`` / ``altai rule``).

One caller is not a host agent: :mod:`altai.loop` records a `product-decisions`
entry for each recommendation it promotes. That is still a decision, not an
inference — the run decided to adopt the candidate and acted on it in the same
pass, and the note is what tells a later task why the resulting task exists.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text, init_workspace, state_lock, workspace_path

MEMORY_DIRNAME = "memory"
RULES_FILENAME = "learned-rules.json"
MAX_NOTE_CHARS = 2000
MAX_DIGEST_ENTRIES = 3
MAX_DIGEST_RULES = 5

CATEGORY_FILES: dict[str, str] = {
    "architecture": "architecture.md",
    "product-decisions": "product-decisions.md",
    "coding-conventions": "coding-conventions.md",
    "failed-approaches": "failed-approaches.md",
    "user-preferences": "user-preferences.md",
}

CATEGORY_HEADERS: dict[str, str] = {
    "architecture": "# Architecture\n\nHow this project is structured, and why.\n",
    "product-decisions": "# Product decisions\n\nChoices about what the project should do.\n",
    "coding-conventions": "# Coding conventions\n\nPatterns this project follows that are not "
    "obvious from the style guide alone.\n",
    "failed-approaches": "# Failed approaches\n\nWhat was tried, and why it did not work — so it "
    "is not tried again the same way.\n",
    "user-preferences": "# User preferences\n\nProduct or process decisions the user made or "
    "rejected.\n",
}


def memory_dir(root: Path) -> Path:
    return workspace_path(root) / MEMORY_DIRNAME


def rules_path(root: Path) -> Path:
    return memory_dir(root) / RULES_FILENAME


def category_path(root: Path, category: str) -> Path:
    filename = CATEGORY_FILES.get(category)
    if filename is None:
        raise ValueError(
            f"Unknown memory category {category!r}. Use one of: "
            f"{', '.join(sorted(CATEGORY_FILES))}"
        )
    return memory_dir(root) / filename


def init_memory(root: Path) -> Path:
    """Create the memory directory and its files if missing. Idempotent."""
    init_workspace(root)
    directory = memory_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    for category, filename in CATEGORY_FILES.items():
        path = directory / filename
        if not path.exists():
            path.write_text(CATEGORY_HEADERS[category], encoding="utf-8")
    if not rules_path(root).exists():
        atomic_write_text(rules_path(root), "[]\n", prefix=".rules-")
    return directory


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def record(root: Path, category: str, note: str, task_id: str = "") -> Path:
    """Append one timestamped entry to a category file. Returns its path.

    ``init_memory`` and the append both happen under the project's own
    ``state_lock``. Without it, two concurrent first-time ``altai learn``
    calls for the same category can both observe the file missing and race
    to write its header — one process's header ``write_text()`` can land
    after the other's append and silently erase it. Same discipline
    :func:`record_rule` already applies to the rules file.
    """
    path = category_path(root, category)  # validates before touching disk/lock
    note = note.strip()[:MAX_NOTE_CHARS]
    if not note:
        raise ValueError("Memory note must not be empty.")
    with state_lock(root):
        init_memory(root)
        tag = f" [{task_id}]" if task_id else ""
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {_stamp()}{tag}: {note}\n")
        return path


def _load_rules(root: Path) -> list[dict[str, Any]]:
    path = rules_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def record_rule(root: Path, condition: str, rule: str, task_id: str = "") -> Path:
    """Append a `{condition, rule}` pair to `learned-rules.json`.

    Unlike the category files, this is meant to be re-read mechanically (by
    :func:`digest`, and eventually by whatever else wants to check a rule
    before acting), so it stays structured instead of prose.

    Both ``init_memory`` (which can itself create ``learned-rules.json`` if
    missing) and the read-modify-write are done under the project's own
    ``state_lock``. Without it, two concurrent ``altai rule`` calls can both
    read the same list before either writes it back, and the second write
    silently discards the first call's rule even though the file stays valid
    JSON — a lost update with no visible error. Racing the *creation* of the
    file has the same failure mode plus, on Windows, a `PermissionError` from
    two threads renaming a temp file onto the same destination at once.
    """
    condition = condition.strip()[:MAX_NOTE_CHARS]
    rule = rule.strip()[:MAX_NOTE_CHARS]
    if not condition or not rule:
        raise ValueError("Both condition and rule must be non-empty.")
    with state_lock(root):
        init_memory(root)
        rules = _load_rules(root)
        rules.append(
            {"condition": condition, "rule": rule, "task_id": task_id, "learned_at": _stamp()}
        )
        path = rules_path(root)
        atomic_write_text(
            path, json.dumps(rules, ensure_ascii=False, indent=2) + "\n", prefix=".rules-"
        )
        return path


def load_rules(root: Path) -> list[dict[str, Any]]:
    return _load_rules(root)


def recent_entries(root: Path, category: str, limit: int = MAX_DIGEST_ENTRIES) -> list[str]:
    """Last *limit* recorded entries in a category, oldest of the batch first."""
    path = category_path(root, category)
    if not path.exists():
        return []
    lines = [
        line
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.startswith("- ")
    ]
    return lines[-limit:] if limit > 0 else []


def has_content(root: Path) -> bool:
    return any(recent_entries(root, category, limit=1) for category in CATEGORY_FILES) or bool(
        load_rules(root)
    )


def digest(root: Path, limit_per_category: int = MAX_DIGEST_ENTRIES) -> str:
    """Short prose summary of everything memory holds, for the next task's brief.

    Empty categories are omitted rather than printed as headers with nothing
    under them — a brief the agent skims should not spend lines on absence.
    """
    parts: list[str] = []
    for category in CATEGORY_FILES:
        lines = recent_entries(root, category, limit_per_category)
        if lines:
            parts.append(f"{category}:\n" + "\n".join(lines))
    rules = load_rules(root)
    if rules:
        recent_rules = rules[-MAX_DIGEST_RULES:]
        parts.append(
            "rules:\n"
            + "\n".join(f"- if {r.get('condition', '')}: {r.get('rule', '')}" for r in recent_rules)
        )
    return "\n\n".join(parts)
