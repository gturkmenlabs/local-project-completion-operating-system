from altai.intelligence.code_graph import build_code_graph
from altai.intelligence.opportunity_finder import (
    find_opportunities,
    load_opportunities,
    opportunities_path,
    opportunity_research_brief,
    save_opportunities,
)
from altai.intelligence.project_model import ProjectModel
from altai.orchestrator import bootstrap


def _model(root, **overrides) -> ProjectModel:
    base = dict(purpose="x", target_user="x", core_flow=[], non_goals=[], tests=[])
    base.update(overrides)
    model = ProjectModel(root=root, name="x", **base)
    model.needs_review = []
    return model


def _write_large_function(path, lines=80):
    body = "\n".join(f"    x{i} = {i}" for i in range(lines))
    path.write_text(f"def big_function():\n{body}\n    return x0\n", encoding="utf-8")


def test_large_function_is_flagged(tmp_path):
    _write_large_function(tmp_path / "app.py")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)

    candidates = find_opportunities(model, graph)

    kinds = {c.kind for c in candidates}
    assert "large-function" in kinds
    large = next(c for c in candidates if c.kind == "large-function")
    assert large.file == "app.py"
    assert large.score_breakdown["complexity"] > 0


def test_small_function_is_not_flagged(tmp_path):
    (tmp_path / "app.py").write_text("def small():\n    return 1\n", encoding="utf-8")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)

    candidates = find_opportunities(model, graph)

    assert candidates == []


def test_duplicated_function_name_across_files_is_flagged(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text("def normalize():\n    pass\n", encoding="utf-8")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)

    candidates = find_opportunities(model, graph)

    dup = next((c for c in candidates if c.kind == "possible-duplication"), None)
    assert dup is not None
    assert "normalize" in dup.title


def test_two_occurrences_do_not_count_as_duplication(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.py").write_text("def normalize():\n    pass\n", encoding="utf-8")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)

    candidates = find_opportunities(model, graph)

    assert not any(c.kind == "possible-duplication" for c in candidates)


def test_high_fanin_function_without_a_test_file_is_flagged(tmp_path):
    (tmp_path / "core.py").write_text("def widely_used():\n    pass\n", encoding="utf-8")
    (tmp_path / "callers.py").write_text(
        "from core import widely_used\n\n"
        "def a():\n    widely_used()\n\n"
        "def b():\n    widely_used()\n\n"
        "def c():\n    widely_used()\n",
        encoding="utf-8",
    )
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path, tests=[])

    candidates = find_opportunities(model, graph)

    assert any(c.kind == "high-fanin-untested" and c.file == "core.py" for c in candidates)


def test_high_fanin_function_with_a_test_file_is_not_flagged(tmp_path):
    (tmp_path / "core.py").write_text("def widely_used():\n    pass\n", encoding="utf-8")
    (tmp_path / "callers.py").write_text(
        "def a():\n    widely_used()\n\ndef b():\n    widely_used()\n\ndef c():\n    widely_used()\n",
        encoding="utf-8",
    )
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path, tests=["core.py"])

    candidates = find_opportunities(model, graph)

    assert not any(c.kind == "high-fanin-untested" and c.file == "core.py" for c in candidates)


def test_non_goal_excludes_a_candidate(tmp_path):
    (tmp_path / "billing.py").write_text(
        "def billing_helper():\n" + "\n".join(f"    x{i} = {i}" for i in range(80)) + "\n",
        encoding="utf-8",
    )
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path, non_goals=["touch billing code"])

    candidates = find_opportunities(model, graph)

    assert not any(c.file == "billing.py" for c in candidates)


def test_core_flow_match_raises_purpose_contribution(tmp_path):
    _write_large_function(tmp_path / "auth.py")
    graph = build_code_graph(tmp_path)
    matched = _model(tmp_path, core_flow=["handle auth requests"])
    unmatched = _model(tmp_path, core_flow=[])

    matched_score = find_opportunities(matched, graph)[0].score
    unmatched_score = find_opportunities(unmatched, graph)[0].score

    assert matched_score > unmatched_score


def test_exclude_ids_filters_already_promoted_candidates(tmp_path):
    _write_large_function(tmp_path / "app.py")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)
    first = find_opportunities(model, graph)
    assert first

    filtered = find_opportunities(model, graph, exclude_ids={first[0].id})

    assert first[0].id not in {c.id for c in filtered}


def test_sorted_highest_score_first(tmp_path):
    _write_large_function(tmp_path / "small.py", lines=61)
    _write_large_function(tmp_path / "large.py", lines=200)
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)

    candidates = find_opportunities(model, graph)

    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_save_and_load_round_trip(tmp_path):
    _write_large_function(tmp_path / "app.py")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)
    candidates = find_opportunities(model, graph)

    path = save_opportunities(tmp_path, candidates)

    assert path == opportunities_path(tmp_path)
    loaded = load_opportunities(tmp_path)
    assert [c.id for c in loaded] == [c.id for c in candidates]


def test_load_opportunities_returns_empty_list_when_absent(tmp_path):
    assert load_opportunities(tmp_path) == []


def test_opportunity_research_brief_reuses_research_module(tmp_path):
    _write_large_function(tmp_path / "app.py")
    graph = build_code_graph(tmp_path)
    model = _model(tmp_path)
    candidate = find_opportunities(model, graph)[0]

    brief = opportunity_research_brief(tmp_path, candidate, ["Python"])

    assert brief.task_id == candidate.id
    assert candidate.title in brief.queries[0]


def test_bootstrap_persists_opportunities_without_injecting_tasks(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndescription='does a thing'\n", encoding="utf-8"
    )
    _write_large_function(tmp_path / "app.py")

    state = bootstrap(tmp_path)

    candidates = load_opportunities(tmp_path)
    assert any(c.kind == "large-function" for c in candidates)
    # Advisory only: never auto-injected as a task, unlike gap_tasks.
    assert not any(t.id.startswith("opp-") for t in state.tasks)
