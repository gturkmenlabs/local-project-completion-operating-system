"""Run the work, instead of only describing it.

Everything else in ALTAI is deterministic bookkeeping: it decides *which* task
is next and *what* would prove it done. Implementation always belonged to a host
agent that a human drove by hand, one task per prompt. This module closes that
gap by launching the host agent's own CLI in headless mode, one invocation per
task, and by running the project's declared verification commands afterwards so
the result is evidence rather than a claim.

ALTAI still has no model access of its own (see :mod:`altai.research`). It shells
out to a CLI the operator already installed and signed in to — `claude` or
`codex` — or to whatever ``ALTAI_AGENT_CMD`` names. Nothing here talks to a model
API, and nothing here is a sandbox: the spawned agent has exactly the
permissions its own configuration grants it.

Nested runs are refused by default. Inside Claude Code, `claude` is on PATH, so
auto-detection would otherwise spawn a second agent underneath the one already
driving the loop — the caller is the agent, and it should receive the task
rather than a subprocess.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Per-invocation wall clock ceiling. A task that needs longer than this has
#: gone wrong in a way another minute will not fix.
DEFAULT_AGENT_TIMEOUT = 1800.0
DEFAULT_CHECK_TIMEOUT = 900.0

#: Names an explicit command template, e.g.
#: ``ALTAI_AGENT_CMD="my-agent --headless {prompt}"``. Without a ``{prompt}``
#: placeholder the prompt is appended as the final argument.
ENV_AGENT_CMD = "ALTAI_AGENT_CMD"
#: Set by the host agents themselves; presence means "you are already inside one".
NESTING_MARKERS = ("CLAUDECODE", "CLAUDE_CODE", "CODEX_SANDBOX", "ALTAI_INSIDE_AGENT")

#: Command labels from `project-model.json` worth running as verification, in
#: reporting order. `start` and `dev` are deliberately absent: they do not
#: terminate.
CHECK_LABELS = ("test", "build", "lint", "typecheck", "check")

#: How much of a command's output is kept in the report. Enough to diagnose,
#: bounded so a run report stays readable and a state file stays small.
OUTPUT_TAIL_CHARS = 2000


def _tail(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= OUTPUT_TAIL_CHARS:
        return text
    return "..." + text[-OUTPUT_TAIL_CHARS:]


@dataclass(slots=True)
class AgentSpec:
    """A resolved host-agent CLI invocation."""

    name: str
    argv: list[str]
    source: str = "detected"
    #: True when argv already contains the prompt placeholder.
    templated: bool = False

    def command_for(self, prompt: str) -> list[str]:
        if self.templated:
            return [part.replace("{prompt}", prompt) for part in self.argv]
        return [*self.argv, prompt]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "argv": list(self.argv), "source": self.source}


def _claude_argv(bypass: bool) -> list[str]:
    argv = ["claude", "-p", "--output-format", "text"]
    # `bypassPermissions` is what "unattended" means for Claude Code; without it
    # a headless run stops on the first edit it wants to confirm.
    argv += ["--permission-mode", "bypassPermissions" if bypass else "acceptEdits"]
    return argv


def _codex_argv(bypass: bool) -> list[str]:
    argv = ["codex", "exec"]
    argv.append("--dangerously-bypass-approvals-and-sandbox" if bypass else "--full-auto")
    return argv


#: Preference order: the CLI whose skill files this repository ships first.
KNOWN_AGENTS = (("claude", _claude_argv), ("codex", _codex_argv))


def running_inside_agent(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return any(source.get(marker) for marker in NESTING_MARKERS)


def detect_agent(
    explicit: str | None = None,
    bypass: bool = True,
    env: dict[str, str] | None = None,
) -> AgentSpec | None:
    """Resolve the agent CLI to drive, or ``None`` when none is available.

    Order: an explicit ``--agent`` value, then ``ALTAI_AGENT_CMD``, then the
    first known CLI on PATH. ``none`` in either override disables execution and
    puts the run into plan-only mode.
    """
    source = env if env is not None else os.environ
    raw = explicit or source.get(ENV_AGENT_CMD) or ""
    raw = raw.strip()
    if raw.lower() in {"none", "off", "false"}:
        return None
    if raw:
        parts = shlex.split(raw)
        if not parts:
            return None
        # A bare CLI name gets that CLI's known unattended flags; a full command
        # line is taken exactly as written, because the operator spelled it out.
        known = dict(KNOWN_AGENTS)
        if len(parts) == 1 and parts[0] in known:
            return AgentSpec(name=parts[0], argv=known[parts[0]](bypass), source="explicit")
        return AgentSpec(
            name=Path(parts[0]).name,
            argv=parts,
            source="explicit",
            templated=any("{prompt}" in part for part in parts),
        )
    for name, builder in KNOWN_AGENTS:
        if shutil.which(name):
            return AgentSpec(name=name, argv=builder(bypass), source="detected")
    return None


@dataclass(slots=True)
class ExecutionResult:
    """Outcome of one agent or check invocation."""

    command: str
    exit_code: int
    duration: float
    output: str = ""
    timed_out: bool = False
    label: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def evidence(self) -> str:
        if self.timed_out:
            return f"{self.command} -> timeout after {self.duration:.0f}s"
        return f"{self.command} -> exit {self.exit_code} in {self.duration:.0f}s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration": round(self.duration, 1),
            "timed_out": self.timed_out,
            "ok": self.ok,
            "output": self.output,
        }


def _run(
    argv: list[str] | str,
    root: Path,
    timeout: float,
    label: str,
    shell: bool = False,
) -> ExecutionResult:
    printable = argv if isinstance(argv, str) else shlex.join(argv)
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - launching the operator's own CLI is the point
            argv,
            cwd=str(root),
            shell=shell,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as expired:
        return ExecutionResult(
            command=printable,
            exit_code=124,
            duration=time.monotonic() - started,
            output=_tail((expired.stdout or "") if isinstance(expired.stdout, str) else ""),
            timed_out=True,
            label=label,
        )
    except (OSError, ValueError) as error:
        # A missing binary or an unparseable command line must not abort the
        # whole run: it is one failed attempt like any other.
        return ExecutionResult(
            command=printable,
            exit_code=127,
            duration=time.monotonic() - started,
            output=str(error),
            label=label,
        )
    return ExecutionResult(
        command=printable,
        exit_code=completed.returncode,
        duration=time.monotonic() - started,
        output=_tail(f"{completed.stdout}\n{completed.stderr}"),
        label=label,
    )


def run_agent(
    spec: AgentSpec,
    prompt: str,
    root: Path,
    timeout: float = DEFAULT_AGENT_TIMEOUT,
) -> ExecutionResult:
    """Run one headless host-agent invocation for one task."""
    return _run(spec.command_for(prompt), Path(root), timeout, label=f"agent:{spec.name}")


def project_checks(commands: dict[str, str] | None, extra: list[str] | None = None) -> list[tuple[str, str]]:
    """Verification commands for this project, declared-first then operator-supplied.

    Only labels in :data:`CHECK_LABELS` are used, so a `dev` or `start` script
    is never launched as a check — those never exit.
    """
    ordered: list[tuple[str, str]] = []
    for label in CHECK_LABELS:
        command = (commands or {}).get(label)
        if isinstance(command, str) and command.strip():
            ordered.append((label, command.strip()))
    for command in extra or []:
        if command.strip():
            ordered.append(("custom", command.strip()))
    return ordered


def run_checks(
    checks: list[tuple[str, str]],
    root: Path,
    timeout: float = DEFAULT_CHECK_TIMEOUT,
) -> list[ExecutionResult]:
    """Run every check, in order, without stopping at the first failure.

    One report that names all three broken gates is worth more to the next
    attempt than one that names the first.
    """
    results = []
    for label, command in checks:
        # Shell, because these come from package.json / Makefile / pyproject and
        # are written as shell (`npm run test`, `a && b`). They are the project's
        # own commands, run in the project's own directory.
        results.append(_run(command, Path(root), timeout, label=label, shell=True))
    return results


PROMPT_TEMPLATE = """You are the implementation agent for one ALTAI task in {root}.

TASK {task_id}: {title}
{description}
ACCEPTANCE CRITERIA
{acceptance}

{extra}
RULES
- Implement this task only. Do not start unrelated work.
- Make the smallest complete change that satisfies every acceptance criterion.
- Run the project's own tests before you finish; fix what you break.
- Do NOT run `altai done`, `altai fail`, `altai block` or edit `.altai/project-state.json`.
  The runner that launched you records the outcome and re-verifies independently.
- Finish with a two-line summary: what changed, and how you verified it.
"""


def build_prompt(brief: dict[str, Any], root: Path, autonomy_note: str = "") -> str:
    """Turn a `next_brief` payload into one self-contained agent prompt."""
    task = brief.get("task", {})
    acceptance = task.get("acceptance") or ["The task's title is satisfied and the project still builds."]
    sections = []
    research = brief.get("research") or {}
    queries = research.get("queries") or []
    if queries:
        sections.append(
            "RESEARCH (only if the task needs it; save a compressed note to "
            f"{research.get('note_path', '.altai/research/')})\n"
            + "\n".join(f"- {query}" for query in queries[:4])
        )
    if brief.get("related_files"):
        sections.append("LIKELY FILES\n" + "\n".join(f"- {item}" for item in brief["related_files"][:8]))
    if brief.get("memory"):
        sections.append("PROJECT MEMORY\n" + str(brief["memory"]).strip())
    if task.get("attempts"):
        # Without this, a retry gets the identical prompt and repeats the
        # identical failure until the attempt budget blocks the task.
        sections.append(
            f"PREVIOUS ATTEMPTS: {task['attempts']} of {task.get('max_attempts', 3)} already "
            f"failed. Last recorded cause: {task.get('notes') or 'not recorded'}. "
            "Change the approach; do not repeat the failed one."
        )
    if autonomy_note:
        sections.append(f"AUTONOMY\n{autonomy_note}")
    description = task.get("description") or ""
    return PROMPT_TEMPLATE.format(
        root=Path(root),
        task_id=task.get("id", "?"),
        title=task.get("title", ""),
        description=f"SOURCE: {description}\n" if description else "",
        acceptance="\n".join(f"- {item}" for item in acceptance),
        extra="\n\n".join(sections) + ("\n" if sections else ""),
    )


@dataclass(slots=True)
class ExecutionPlan:
    """What one iteration intends to run, resolved once per run."""

    agent: AgentSpec | None
    checks: list[tuple[str, str]] = field(default_factory=list)
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT
    check_timeout: float = DEFAULT_CHECK_TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent.to_dict() if self.agent else None,
            "checks": [{"label": label, "command": command} for label, command in self.checks],
            "agent_timeout": self.agent_timeout,
            "check_timeout": self.check_timeout,
        }
