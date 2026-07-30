import subprocess

import pytest

from altai.checkpoint import Checkpointer, head, is_clean, is_repository


def _git(root, *args, identity=True):
    prefix = ["-c", "user.name=Test", "-c", "user.email=test@example.com"] if identity else []
    return subprocess.run(
        ["git", *prefix, *args], cwd=str(root), capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_a_non_repository_is_never_checkpointed(tmp_path):
    checkpointer = Checkpointer.start(tmp_path)

    assert is_repository(tmp_path) is False
    assert checkpointer.enabled is False
    assert "not a git repository" in checkpointer.reason


def test_clean_repository_gets_commits_and_rollback(repo):
    checkpointer = Checkpointer.start(repo)

    assert checkpointer.enabled is True and checkpointer.rollback is True
    assert checkpointer.baseline == head(repo)


def test_a_dirty_tree_disables_checkpointing_rather_than_burying_the_work(repo):
    (repo / "wip.py").write_text("unfinished\n", encoding="utf-8")

    checkpointer = Checkpointer.start(repo)

    assert checkpointer.enabled is False
    assert "already dirty" in checkpointer.reason
    assert (repo / "wip.py").exists(), "the operator's work must be untouched"


def test_forcing_commits_on_a_dirty_tree_still_refuses_rollback(repo):
    (repo / "wip.py").write_text("unfinished\n", encoding="utf-8")

    checkpointer = Checkpointer.start(repo, commit=True)

    assert checkpointer.enabled is True
    assert checkpointer.rollback is False, "a reset here would delete work the run did not write"


def test_commit_task_records_one_commit_per_task(repo):
    checkpointer = Checkpointer.start(repo)
    (repo / "feature.py").write_text("y = 2\n", encoding="utf-8")

    sha = checkpointer.commit_task("task-1", "Add the feature", "pytest -> exit 0")

    assert sha and sha == head(repo)
    assert is_clean(repo)
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s%n%b"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert "altai(task-1): Add the feature" in log
    assert "pytest -> exit 0" in log


def test_a_task_that_changed_nothing_makes_no_commit(repo):
    checkpointer = Checkpointer.start(repo)
    before = head(repo)

    assert checkpointer.commit_task("task-1", "Nothing to do") == ""
    assert head(repo) == before


def test_rollback_discards_the_failed_attempt_including_new_files(repo):
    checkpointer = Checkpointer.start(repo)
    (repo / "app.py").write_text("broken\n", encoding="utf-8")
    (repo / "half-written.py").write_text("oops\n", encoding="utf-8")

    assert checkpointer.rollback_task() is True
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (repo / "half-written.py").exists()


def test_rollback_never_touches_ignored_state(repo):
    (repo / ".gitignore").write_text(".altai/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore altai")
    checkpointer = Checkpointer.start(repo)
    (repo / ".altai").mkdir()
    (repo / ".altai" / "project-state.json").write_text("{}", encoding="utf-8")
    (repo / "app.py").write_text("broken\n", encoding="utf-8")

    assert checkpointer.rollback_task() is True
    # The run's own record of what happened must survive the rollback.
    assert (repo / ".altai" / "project-state.json").exists()


def test_rollback_stops_at_the_previous_checkpoint_not_the_run_start(repo):
    checkpointer = Checkpointer.start(repo)
    (repo / "first.py").write_text("done\n", encoding="utf-8")
    first = checkpointer.commit_task("task-1", "First task")
    (repo / "second.py").write_text("broken\n", encoding="utf-8")

    checkpointer.rollback_task()

    assert head(repo) == first
    assert (repo / "first.py").exists(), "a completed task must survive a later failure"
    assert not (repo / "second.py").exists()


def test_commit_works_without_a_configured_git_identity(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    # Explicitly blank the identity for this repository only.
    _git(tmp_path, "config", "user.email", "")
    _git(tmp_path, "config", "user.name", "")
    checkpointer = Checkpointer.start(tmp_path)
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

    sha = checkpointer.commit_task("task-1", "Add b")

    assert sha, "a missing git identity must not silently lose the checkpoint"


def test_disabled_checkpointer_is_inert(repo):
    checkpointer = Checkpointer.start(repo, commit=False)
    (repo / "new.py").write_text("z = 3\n", encoding="utf-8")

    assert checkpointer.commit_task("t", "t") == ""
    assert checkpointer.rollback_task() is False
    assert (repo / "new.py").exists()
