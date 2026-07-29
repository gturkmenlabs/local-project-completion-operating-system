"""Compare `.altai/project-model.json` against what the repository actually has.

:mod:`.project_model` answers "what is this project for". The scanner answers
"what markers are unresolved". Neither one notices that a project claims a test
command with no tests behind it, or ships an entry point nobody can launch —
the gap between *declared* and *observed* that a marker scan cannot see because
no file contains a TODO for it.

Each check here stays mechanical, the same rule :mod:`.project_model` follows:
a gap is reported only when the model's own declared fields contradict its own
derived fields, never from guessing at intent. The host agent still decides
what to do about it; this module only points at the contradiction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Task
from .project_model import ProjectModel

CONFIRM_MODEL_ID = "gap-confirm-project-model"
MISSING_TESTS_ID = "gap-missing-tests"
UNDOCUMENTED_TESTS_ID = "gap-undocumented-test-command"
NO_RUN_COMMAND_ID = "gap-no-run-command"


@dataclass(slots=True)
class Gap:
    """A single contradiction between what the model declares and what it derived."""

    id: str
    title: str
    description: str
    acceptance: list[str] = field(default_factory=list)


def find_gaps(model: ProjectModel) -> list[Gap]:
    """Return every detected gap for *model*, most consequential first."""
    gaps: list[Gap] = []
    gaps.extend(_model_confirmation_gap(model))
    gaps.extend(_test_command_gaps(model))
    gaps.extend(_run_command_gap(model))
    return gaps


def gap_tasks(model: ProjectModel) -> list[Task]:
    """Render :func:`find_gaps` as discovered :class:`Task` objects.

    IDs are fixed per gap kind rather than content-hashed: a gap is a condition
    on the model ("purpose unconfirmed"), not a line in a file, so there is only
    ever one instance of it open at a time. That lets a rescan's task list be
    diffed against the previous one by :func:`altai.memory.merge_state` exactly
    like a discovered TODO — the task disappears on its own once the condition
    that produced it clears.
    """
    return [
        Task(
            id=gap.id,
            title=gap.title,
            description=gap.description,
            acceptance=list(gap.acceptance),
            discovered=True,
        )
        for gap in find_gaps(model)
    ]


def _model_confirmation_gap(model: ProjectModel) -> list[Gap]:
    if not model.needs_review:
        return []
    fields_text = ", ".join(model.needs_review)
    return [
        Gap(
            id=CONFIRM_MODEL_ID,
            title="Confirm project purpose, audience, flow and non-goals",
            description=(
                f"{fields_text} in .altai/project-model.json still hold a value scraped "
                "from documentation rather than one the host agent confirmed. Read the "
                "repository, then rewrite each field so it no longer appears in `derived`."
            ),
            acceptance=[f"'{name}' removed from needs_review" for name in model.needs_review],
        )
    ]


def _test_command_gaps(model: ProjectModel) -> list[Gap]:
    has_command = bool(model.commands.get("test"))
    has_tests = bool(model.tests)
    if has_command and not has_tests:
        return [
            Gap(
                id=MISSING_TESTS_ID,
                title="Declared test command has no tests to run",
                description=(
                    f"commands.test = {model.commands['test']!r} but no test file was found "
                    "under tests/, test/, spec/ or __tests__/. Either the test suite is "
                    "missing or it lives somewhere the scanner does not look."
                ),
                acceptance=["A test file exists", "The declared test command exits 0"],
            )
        ]
    if has_tests and not has_command:
        return [
            Gap(
                id=UNDOCUMENTED_TESTS_ID,
                title="Tests exist but no command runs them",
                description=(
                    f"{len(model.tests)} test file(s) were found, but no manifest, script or "
                    "Makefile target declares how to run them. Add a documented test command."
                ),
                acceptance=["commands.test set in project-model.json", "Command runs the tests"],
            )
        ]
    return []


def _run_command_gap(model: ProjectModel) -> list[Gap]:
    if model.entry_points and not model.commands:
        return [
            Gap(
                id=NO_RUN_COMMAND_ID,
                title="Entry point exists but no command launches it",
                description=(
                    f"Entry point(s) {', '.join(model.entry_points[:5])} were found, but "
                    "project-model.json has no commands. Document how a user actually runs "
                    "this project."
                ),
                acceptance=["At least one command documented", "Command launches the entry point"],
            )
        ]
    return []
