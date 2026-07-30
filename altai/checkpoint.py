"""Git checkpoints: the undo an unattended run needs.

With approvals disabled, the working tree is the only thing standing between a
wrong turn and lost work. Every serious agent harness answers this the same way
— commit per completed step, isolate, and roll back to the last good point on
failure — so ALTAI does too, using the repository the project already has rather
than a private snapshot format of its own.

Two rules keep it from ever destroying work it did not create:

* Checkpointing is refused unless the working tree is **clean** at the start of
  the run. A dirty tree means uncommitted human work, and neither a commit
  (which would bury it inside an ALTAI commit) nor a reset (which would delete
  it) is acceptable.
* A rollback only ever targets a commit this run created, or the exact HEAD the
  run started from — never an arbitrary revision.

Untracked files created by a failed attempt are removed with ``git clean -fd``,
which leaves ignored paths alone, so `.altai/` — the state, evidence and audit
trail of the run itself — survives every rollback.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Git is only ever asked for cheap, local operations here.
GIT_TIMEOUT = 120.0
COMMIT_PREFIX = "altai"
#: Used only when the repository has no identity configured at all.
FALLBACK_NAME = "ALTAI"
FALLBACK_EMAIL = "altai@localhost"
_IDENTITY_RE = re.compile(r"tell me who you are|user\.email|unable to auto-detect", re.I)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=GIT_TIMEOUT,
    )


def is_repository(root: Path) -> bool:
    try:
        result = _git(Path(root), "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def head(root: Path) -> str:
    """Current commit, or an empty string on an unborn branch or a non-repo."""
    try:
        result = _git(Path(root), "rev-parse", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def is_clean(root: Path) -> bool:
    """True when nothing is staged, modified or untracked."""
    try:
        result = _git(Path(root), "status", "--porcelain")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and not result.stdout.strip()


@dataclass(slots=True)
class Checkpointer:
    """Per-task commits and rollback for one run.

    ``enabled`` is decided once, at run start, and never re-derived: a tree that
    became dirty because the agent edited it is expected, whereas a tree that was
    dirty before the run began is somebody else's unfinished work.
    """

    root: Path
    enabled: bool = False
    rollback: bool = False
    #: Commit the run started from; the floor a rollback can never go below.
    baseline: str = ""
    reason: str = ""

    @classmethod
    def start(
        cls,
        root: Path,
        commit: bool | None = None,
        rollback: bool | None = None,
    ) -> "Checkpointer":
        """Decide the policy for this run.

        *commit* ``None`` means "on when it is safe": a git repository with a
        clean working tree. ``True`` forces it on a dirty tree — the operator's
        call — but rollback still stays off there, because the baseline would
        include work this run did not write.
        """
        root = Path(root).resolve()
        if commit is False:
            return cls(root=root, reason="disabled by --no-commit")
        if not is_repository(root):
            return cls(root=root, reason="not a git repository")
        current = head(root)
        if not current:
            return cls(root=root, reason="no commit on HEAD yet")
        clean = is_clean(root)
        if not clean and commit is not True:
            return cls(
                root=root,
                reason="working tree was already dirty; per-task commits and rollback are off "
                "so uncommitted work is neither buried nor discarded",
            )
        allow_rollback = clean if rollback is None else (rollback and clean)
        return cls(
            root=root,
            enabled=True,
            rollback=allow_rollback,
            baseline=current,
            reason="" if clean else "committing on a dirty tree by request; rollback disabled",
        )

    def commit_task(self, task_id: str, title: str, body: str = "") -> str:
        """Commit everything the task changed. Returns the sha, or ``""``."""
        if not self.enabled:
            return ""
        try:
            if _git(self.root, "add", "-A").returncode != 0:
                return ""
            if _git(self.root, "diff", "--cached", "--quiet").returncode == 0:
                return ""  # the task changed nothing; an empty commit says nothing
            message = f"{COMMIT_PREFIX}({task_id}): {title}".strip()
            if body:
                message += f"\n\n{body}"
            result = _git(self.root, "commit", "-m", message, "--no-verify")
            if result.returncode != 0:
                if not _IDENTITY_RE.search(result.stderr or ""):
                    return ""
                # No git identity configured — common on a fresh CI runner. Use a
                # run-scoped one rather than losing the checkpoint; this never
                # overwrites the repository's own config.
                result = _git(
                    self.root,
                    "-c",
                    f"user.name={FALLBACK_NAME}",
                    "-c",
                    f"user.email={FALLBACK_EMAIL}",
                    "commit",
                    "-m",
                    message,
                    "--no-verify",
                )
                if result.returncode != 0:
                    return ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
        sha = head(self.root)
        self.baseline = sha or self.baseline
        return sha

    def rollback_task(self) -> bool:
        """Discard everything since the last checkpoint. Returns whether it ran."""
        if not (self.enabled and self.rollback and self.baseline):
            return False
        try:
            if _git(self.root, "reset", "--hard", self.baseline).returncode != 0:
                return False
            # Ignored paths are left alone, so .altai/ (state, evidence, run log)
            # survives the rollback that discards the failed attempt's code.
            _git(self.root, "clean", "-fd")
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True

    def diff_since_start(self, start: str) -> str:
        """One-line-per-file summary of everything the run committed."""
        if not (self.enabled and start):
            return ""
        try:
            result = _git(self.root, "diff", "--stat", f"{start}..HEAD")
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""
