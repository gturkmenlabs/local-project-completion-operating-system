from altai.scanner import scan_project, task_id_for_marker


def test_detects_python_and_todo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    (tmp_path / "app.py").write_text("# TODO: add login\n", encoding="utf-8")
    state = scan_project(tmp_path)
    assert "Python" in state.stack
    assert any("add login" in task.title for task in state.tasks)


def test_task_ids_are_content_addressed_not_positional(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("# TODO: add login\n# FIXME: fix parser\n", encoding="utf-8")
    before = {task.title: task.id for task in scan_project(tmp_path).tasks}

    # Resolve the first marker. The second task must keep the same ID.
    source.write_text("# FIXME: fix parser\n", encoding="utf-8")
    after = {task.title: task.id for task in scan_project(tmp_path).tasks}

    assert after["fix parser"] == before["fix parser"]
    assert "add login" not in after


def test_ignored_directories_are_not_scanned(tmp_path):
    for folder in ("node_modules", ".venv", "dist", "__pycache__", ".altai", ".claude"):
        target = tmp_path / folder
        target.mkdir()
        (target / "junk.py").write_text("# TODO: ignore me\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("# TODO: keep me\n", encoding="utf-8")
    titles = [task.title for task in scan_project(tmp_path).tasks]
    assert titles == ["keep me"]


def test_duplicate_markers_collapse(tmp_path):
    (tmp_path / "a.py").write_text("# TODO: same\n# TODO: same\n", encoding="utf-8")
    tasks = scan_project(tmp_path).tasks
    assert len([t for t in tasks if t.title == "same"]) == 1


def test_marker_id_is_stable_across_runs():
    assert task_id_for_marker("src/a.py", "Add login") == task_id_for_marker(
        "src/a.py", "add login"
    )
    assert task_id_for_marker("src/a.py", "x") != task_id_for_marker("src/b.py", "x")


def test_baseline_task_when_nothing_found(tmp_path):
    (tmp_path / "empty.txt").write_text("nothing here", encoding="utf-8")
    state = scan_project(tmp_path)
    assert [task.id for task in state.tasks] == ["baseline-verification"]
