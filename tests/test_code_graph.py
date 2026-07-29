from altai.intelligence.code_graph import (
    build_code_graph,
    graph_path,
    load_graph,
    related_files,
    save_graph,
)
from altai.orchestrator import bootstrap


def test_python_ast_extracts_classes_functions_methods_and_calls(tmp_path):
    (tmp_path / "auth.py").write_text(
        """
def helper():
    pass


class LoginService:
    def login(self, user):
        return self.check(user) and helper()

    def check(self, user):
        return True
""",
        encoding="utf-8",
    )

    graph = build_code_graph(tmp_path)

    names = {s.name: s for s in graph.symbols}
    assert names["helper"].kind == "function"
    assert names["LoginService"].kind == "class"
    assert names["login"].kind == "method"
    assert "check" in names["login"].calls
    assert "helper" in names["login"].calls
    assert "auth.py" in graph.files


def test_syntax_error_file_is_skipped_not_fatal(tmp_path):
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")

    graph = build_code_graph(tmp_path)

    assert "broken.py" in graph.skipped
    assert "broken.py" not in graph.files


def test_regex_pass_extracts_top_level_declarations_for_other_languages(tmp_path):
    (tmp_path / "app.js").write_text(
        "function loginUser(user) {}\n\nclass SessionManager {}\n", encoding="utf-8"
    )
    (tmp_path / "main.go").write_text("func StartServer() {}\n", encoding="utf-8")

    graph = build_code_graph(tmp_path)

    names = {s.name for s in graph.symbols}
    assert "loginUser" in names
    assert "SessionManager" in names
    assert "StartServer" in names
    # Regex-derived symbols never claim a call graph they cannot back up.
    assert all(s.calls == [] for s in graph.symbols if s.file.endswith((".js", ".go")))


def test_ignored_directories_are_never_walked(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("function x() {}\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def y():\n    pass\n", encoding="utf-8")

    graph = build_code_graph(tmp_path)

    assert "real.py" in graph.files
    assert not any("node_modules" in f for f in graph.files)


def test_save_and_load_round_trip(tmp_path):
    (tmp_path / "x.py").write_text("def x():\n    pass\n", encoding="utf-8")
    graph = build_code_graph(tmp_path)

    path = save_graph(graph)

    assert path == graph_path(tmp_path)
    loaded = load_graph(tmp_path)
    assert loaded == graph


def test_load_graph_returns_none_when_absent(tmp_path):
    assert load_graph(tmp_path) is None


def test_related_files_ranks_by_word_overlap(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def login_user():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "billing.py").write_text(
        "def charge_card():\n    pass\n", encoding="utf-8"
    )
    graph = build_code_graph(tmp_path)

    ranked = related_files(graph, "fix login redirect bug")

    assert ranked[0] == "auth.py"
    assert "billing.py" not in ranked


def test_related_files_empty_for_generic_words(tmp_path):
    (tmp_path / "auth.py").write_text("def login_user():\n    pass\n", encoding="utf-8")
    graph = build_code_graph(tmp_path)

    assert related_files(graph, "fix the add") == []


def test_bootstrap_persists_a_code_graph(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")

    bootstrap(tmp_path)

    graph = load_graph(tmp_path)
    assert graph is not None
    assert "app.py" in graph.files
