"""Score improvement candidates the repository did not name by ID.

:mod:`.gap_analyzer` closes a contradiction in intent the repository already
*declares* (a test command with no tests). This module is different in kind:
it *creates* candidate intent — "this is probably worth doing" — which is
exactly what ``CLAUDE.md``'s own operating contract calls an ambiguous product
decision requiring human approval. So a candidate here is never auto-injected
into the task graph the way :func:`.gap_analyzer.gap_tasks` is. It is written
to ``.altai/opportunities.json`` for review, and only becomes real work through
a deliberate, separate action — ``altai promote <opportunity-id>``.

Every score input is mechanically derived from :mod:`.code_graph` and
:mod:`.project_model` — line counts, caller counts, keyword overlap with the
project's own declared ``core_flow``/``non_goals``. There is no web search,
competitor analysis or guessed "market value" here; ALTAI does not fetch
anything on its own (see :mod:`altai.research`). Where the scoring formula has
a term this repository cannot supply mechanically, that term is left at 0
rather than invented — the host agent's own research, via
:func:`opportunity_research_brief`, is where that judgement belongs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..memory import atomic_write_text, init_workspace, workspace_path
from ..research import ResearchBrief, build_research_brief
from .code_graph import CodeGraph, Symbol
from .project_model import ProjectModel

OPPORTUNITIES_FILENAME = "opportunities.json"

#: A function/method this small is not worth flagging for either check below.
LARGE_FUNCTION_LINES = 60
#: Symbol name appears in at least this many distinct files to look duplicated.
DUPLICATION_MIN_FILES = 3
#: Called from at least this many places to count as "load-bearing".
HIGH_FANIN_MIN_CALLERS = 3
MAX_CANDIDATES = 30

_WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")


@dataclass(slots=True)
class OpportunityCandidate:
    """One scored, not-yet-adopted improvement."""

    id: str
    kind: str
    title: str
    description: str
    file: str
    score: float
    #: user_value, purpose_contribution, risk_reduction, complexity, regression_risk
    score_breakdown: dict[str, float] = field(default_factory=dict)
    acceptance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpportunityCandidate":
        known = {f.name for f in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        payload.setdefault("id", "")
        payload.setdefault("kind", "")
        payload.setdefault("title", "")
        payload.setdefault("description", "")
        payload.setdefault("file", "")
        payload["score"] = float(payload.get("score") or 0)
        breakdown = payload.get("score_breakdown") or {}
        payload["score_breakdown"] = {str(k): float(v) for k, v in breakdown.items()}
        payload["acceptance"] = [str(a) for a in payload.get("acceptance") or []]
        return cls(**payload)


def opportunities_path(root: Path) -> Path:
    return workspace_path(root) / OPPORTUNITIES_FILENAME


def save_opportunities(root: Path, candidates: list[OpportunityCandidate]) -> Path:
    init_workspace(root)
    payload = json.dumps([c.to_dict() for c in candidates], ensure_ascii=False, indent=2)
    return atomic_write_text(opportunities_path(root), payload, prefix=".opportunities-")


def load_opportunities(root: Path) -> list[OpportunityCandidate]:
    path = opportunities_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    candidates = []
    for item in data:
        if isinstance(item, dict):
            candidates.append(OpportunityCandidate.from_dict(item))
    return candidates


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _score(
    *, user_value: float, purpose_contribution: float, risk_reduction: float,
    complexity: float, regression_risk: float,
) -> tuple[float, dict[str, float]]:
    breakdown = {
        "user_value": user_value,
        "purpose_contribution": purpose_contribution,
        "risk_reduction": risk_reduction,
        "complexity": complexity,
        "regression_risk": regression_risk,
    }
    total = user_value + purpose_contribution + risk_reduction - complexity - regression_risk
    return total, breakdown


def _candidate_id(kind: str, file: str, name: str) -> str:
    digest = hashlib.sha1(f"{kind}::{file}::{name}".encode("utf-8")).hexdigest()
    return f"opp-{digest[:10]}"


def _touches(model: ProjectModel, wordset_source: list[str], relative_path: str) -> bool:
    """True when *relative_path*'s own words overlap words in *wordset_source*.

    Used both for "does this file serve the declared core flow" (a positive
    signal, feeds ``purpose_contribution``) and "does this file collide with a
    declared non-goal" (a negative gate, see :func:`_excluded_by_non_goals`).
    Same coarse word-overlap heuristic :func:`.code_graph.related_files` uses —
    good enough to gate a score, not precise enough to claim understanding.
    """
    target_words = _words(" ".join(wordset_source))
    if not target_words:
        return False
    path_words = {p for p in re.split(r"[/_.\-]", relative_path.lower()) if len(p) > 2}
    return bool(target_words & path_words)


def _excluded_by_non_goals(model: ProjectModel, relative_path: str, name: str) -> bool:
    if not model.non_goals:
        return False
    return _touches(model, model.non_goals, relative_path) or _touches(model, model.non_goals, name)


def _large_function_candidates(model: ProjectModel, graph: CodeGraph) -> list[OpportunityCandidate]:
    candidates = []
    for symbol in graph.symbols:
        if symbol.kind not in ("function", "method") or symbol.size < LARGE_FUNCTION_LINES:
            continue
        if _excluded_by_non_goals(model, symbol.file, symbol.name):
            continue
        callers = len(graph.callers_of(symbol.name))
        purpose = 1.0 if _touches(model, model.core_flow, symbol.file) else 0.0
        total, breakdown = _score(
            user_value=0.0,
            purpose_contribution=purpose,
            risk_reduction=min(5.0, symbol.size / 30),
            complexity=min(5.0, symbol.size / 20),
            regression_risk=min(5.0, callers / 3),
        )
        candidates.append(
            OpportunityCandidate(
                id=_candidate_id("large-function", symbol.file, symbol.name),
                kind="large-function",
                title=f"Break up {symbol.name} ({symbol.size} lines)",
                description=(
                    f"{symbol.file}:{symbol.line} — {symbol.kind} '{symbol.name}' is "
                    f"{symbol.size} lines with {callers} known caller(s). Long functions are "
                    "harder to test in isolation and more likely to accumulate unrelated "
                    "responsibilities."
                ),
                file=symbol.file,
                score=total,
                score_breakdown=breakdown,
                acceptance=[
                    f"'{symbol.name}' split into smaller, independently testable pieces",
                    "Existing tests (or new ones) still pass",
                ],
            )
        )
    return candidates


def _duplication_candidates(model: ProjectModel, graph: CodeGraph) -> list[OpportunityCandidate]:
    by_name: dict[str, list[Symbol]] = {}
    for symbol in graph.symbols:
        if symbol.kind not in ("function", "method"):
            continue
        by_name.setdefault(symbol.name, []).append(symbol)

    candidates = []
    for name, occurrences in by_name.items():
        files = sorted({s.file for s in occurrences})
        if len(files) < DUPLICATION_MIN_FILES:
            continue
        if _excluded_by_non_goals(model, files[0], name):
            continue
        total_callers = len(graph.callers_of(name))
        purpose = 1.0 if any(_touches(model, model.core_flow, f) for f in files) else 0.0
        total, breakdown = _score(
            user_value=0.0,
            purpose_contribution=purpose,
            risk_reduction=3.0,
            complexity=2.0,
            regression_risk=min(5.0, total_callers / 3),
        )
        candidates.append(
            OpportunityCandidate(
                id=_candidate_id("possible-duplication", files[0], name),
                kind="possible-duplication",
                title=f"Consolidate '{name}' ({len(files)} files declare it)",
                description=(
                    f"'{name}' is declared in {len(files)} files: {', '.join(files[:6])}. Same "
                    "name across this many files often means duplicated logic that now has to "
                    "be fixed N times instead of once."
                ),
                file=files[0],
                score=total,
                score_breakdown=breakdown,
                acceptance=[
                    "Confirmed as true duplication, not coincidental naming",
                    "Consolidated into one implementation or explicitly justified as separate",
                ],
            )
        )
    return candidates


def _high_fanin_untested_candidates(
    model: ProjectModel, graph: CodeGraph
) -> list[OpportunityCandidate]:
    tested_files = set(model.tests)
    candidates = []
    for symbol in graph.symbols:
        if symbol.kind not in ("function", "method"):
            continue
        callers = len(graph.callers_of(symbol.name))
        if callers < HIGH_FANIN_MIN_CALLERS or symbol.file in tested_files:
            continue
        if _excluded_by_non_goals(model, symbol.file, symbol.name):
            continue
        purpose = 1.0 if _touches(model, model.core_flow, symbol.file) else 0.0
        total, breakdown = _score(
            user_value=purpose,
            purpose_contribution=purpose,
            risk_reduction=min(5.0, callers / 2),
            complexity=1.0,
            regression_risk=0.0,
        )
        candidates.append(
            OpportunityCandidate(
                id=_candidate_id("high-fanin-untested", symbol.file, symbol.name),
                kind="high-fanin-untested",
                title=f"Add tests for '{symbol.name}' ({callers} callers, no test file found)",
                description=(
                    f"{symbol.file}:{symbol.line} — '{symbol.name}' has {callers} known "
                    f"caller(s) but its file is not among the {len(tested_files)} test file(s) "
                    "the project model found. A change here has no automated check."
                ),
                file=symbol.file,
                score=total,
                score_breakdown=breakdown,
                acceptance=[f"'{symbol.name}' has at least one passing test"],
            )
        )
    return candidates


def find_opportunities(
    model: ProjectModel, graph: CodeGraph, exclude_ids: set[str] | None = None
) -> list[OpportunityCandidate]:
    """Every detected candidate, highest score first, capped at :data:`MAX_CANDIDATES`.

    *exclude_ids* is normally the current task graph's task IDs — a candidate
    already promoted (its ID becomes the promoted task's ID, see
    :mod:`altai.orchestrator`) must not be suggested again next scan.
    """
    exclude_ids = exclude_ids or set()
    candidates = [
        *_large_function_candidates(model, graph),
        *_duplication_candidates(model, graph),
        *_high_fanin_untested_candidates(model, graph),
    ]
    candidates = [c for c in candidates if c.id not in exclude_ids]
    candidates.sort(key=lambda c: (-c.score, c.id))
    return candidates[:MAX_CANDIDATES]


def opportunity_research_brief(
    root: Path, candidate: OpportunityCandidate, stack: list[str]
) -> ResearchBrief:
    """A research brief framed around *this* candidate, for the host agent's
    own web search — competitive/technique research ALTAI does not do itself."""
    return build_research_brief(root, candidate.title, stack, candidate.id)
