"""Scanner quality regressions, found by running ALTAI against a real repo.

On a Next.js monorepo with the app under `web/`, v0.2.2 reported the stack as
"belirsiz" and produced two tasks titled `';` and `) 555-0100...` scraped from
skill documentation, while the actual source tree had zero markers.
"""

import pytest

from altai.scanner import detect_stack, scan_project


def test_stack_detected_in_subdirectory(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text("{}", encoding="utf-8")
    (web / "tsconfig.json").write_text("{}", encoding="utf-8")
    stack = detect_stack(tmp_path)
    assert "Node.js" in stack and "TypeScript" in stack
    assert "TypeScript" in scan_project(tmp_path).stack


def test_stack_detection_skips_dependency_dirs(tmp_path):
    vendored = tmp_path / "node_modules" / "left-pad"
    vendored.mkdir(parents=True)
    (vendored / "Cargo.toml").write_text("", encoding="utf-8")
    assert "Rust" not in detect_stack(tmp_path)


def test_stack_detection_is_bounded(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "go.mod").write_text("module x", encoding="utf-8")
    assert "Go" not in detect_stack(tmp_path)


@pytest.mark.parametrize(
    "line",
    [
        "const message = 'no TODO here';",
        'log("the TODO list is empty")',
        "Phone numbers TODO are matched by this regex",
    ],
)
def test_prose_and_string_literals_are_not_tasks(tmp_path, line):
    (tmp_path / "app.ts").write_text(line + "\n", encoding="utf-8")
    titles = [t.title for t in scan_project(tmp_path).tasks if t.id.startswith("todo-")]
    assert titles == []


@pytest.mark.parametrize(
    "line, expected",
    [
        ("// TODO: wire up the retry", "wire up the retry"),
        ("  # FIXME: parser breaks on empty input", "parser breaks on empty input"),
        ("code();  // TODO handle the error", "handle the error"),
        ("/* TODO: refactor */", "refactor */"),
    ],
)
def test_real_annotations_are_tasks(tmp_path, line, expected):
    (tmp_path / "app.ts").write_text(line + "\n", encoding="utf-8")
    titles = [t.title for t in scan_project(tmp_path).tasks if t.id.startswith("todo-")]
    assert titles == [expected]


def test_markdown_prose_mentioning_todo_is_ignored(tmp_path):
    (tmp_path / "guide.md").write_text(
        "When you see a TODO in the code, resolve it.\n"
        "The word FIXME means something is broken.\n",
        encoding="utf-8",
    )
    assert [t.title for t in scan_project(tmp_path).tasks if t.id.startswith("todo-")] == []


def test_markdown_checklist_entries_are_tasks(tmp_path):
    (tmp_path / "plan.md").write_text("- TODO: write the migration guide\n", encoding="utf-8")
    titles = [t.title for t in scan_project(tmp_path).tasks if t.id.startswith("todo-")]
    assert titles == ["write the migration guide"]


def test_documentation_only_repo_gets_the_baseline_task(tmp_path):
    docs = tmp_path / ".agents" / "skills" / "example"
    docs.mkdir(parents=True)
    (docs / "reference.md").write_text("Handle the TODO marker like this: `TODO';`\n", "utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}", encoding="utf-8")
    state = scan_project(tmp_path)
    assert [task.id for task in state.tasks] == ["baseline-verification"]
    assert state.stack == ["Node.js"]
