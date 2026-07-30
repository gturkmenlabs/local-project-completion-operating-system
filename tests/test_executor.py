import sys

from altai.executor import (
    AgentSpec,
    build_prompt,
    detect_agent,
    project_checks,
    run_agent,
    run_checks,
    running_inside_agent,
)


def test_detect_agent_none_disables_execution():
    assert detect_agent("none", env={}) is None
    assert detect_agent(None, env={"ALTAI_AGENT_CMD": "off"}) is None


def test_detect_known_agent_gets_unattended_flags():
    spec = detect_agent("claude", bypass=True, env={})

    assert spec.argv[0] == "claude"
    assert "bypassPermissions" in spec.argv
    assert spec.source == "explicit"


def test_detect_known_agent_keeps_approvals_when_not_bypassing():
    spec = detect_agent("claude", bypass=False, env={})

    assert "bypassPermissions" not in spec.argv
    assert "acceptEdits" in spec.argv


def test_explicit_command_line_is_used_verbatim():
    spec = detect_agent("my-agent --headless", env={})

    assert spec.argv == ["my-agent", "--headless"]
    assert spec.command_for("do it") == ["my-agent", "--headless", "do it"]


def test_prompt_placeholder_is_substituted_not_appended():
    spec = detect_agent("my-agent --task {prompt} --quiet", env={})

    assert spec.templated is True
    assert spec.command_for("do it") == ["my-agent", "--task", "do it", "--quiet"]


def test_env_command_is_used_when_no_explicit_agent():
    spec = detect_agent(None, env={"ALTAI_AGENT_CMD": "codex"})

    assert spec.argv[0] == "codex"


def test_path_detection_is_the_last_resort(monkeypatch):
    monkeypatch.setattr("altai.executor.shutil.which", lambda name: name == "codex")

    spec = detect_agent(None, env={})

    assert spec.name == "codex"
    assert spec.source == "detected"


def test_no_agent_anywhere_returns_none(monkeypatch):
    monkeypatch.setattr("altai.executor.shutil.which", lambda name: None)

    assert detect_agent(None, env={}) is None


def test_running_inside_agent_reads_host_markers():
    assert running_inside_agent({"CLAUDECODE": "1"}) is True
    assert running_inside_agent({}) is False


def test_project_checks_skips_never_terminating_commands():
    checks = project_checks(
        {"test": "pytest", "dev": "npm run dev", "start": "npm start", "lint": "ruff check"},
        extra=["python -m compileall -q ."],
    )

    labels = [label for label, _ in checks]
    assert labels == ["test", "lint", "custom"]


def test_run_checks_reports_every_command_not_just_the_first(tmp_path):
    results = run_checks([("test", "exit 3"), ("lint", "exit 0")], tmp_path)

    assert [r.exit_code for r in results] == [3, 0]
    assert results[0].ok is False and results[1].ok is True
    assert "exit 3 -> exit 3" in results[0].evidence


def test_run_agent_reports_a_missing_binary_instead_of_raising(tmp_path):
    spec = AgentSpec(name="nope", argv=["altai-no-such-binary-xyz"])

    result = run_agent(spec, "prompt", tmp_path, timeout=10)

    assert result.ok is False
    assert result.exit_code == 127


def test_run_agent_times_out_without_killing_the_run(tmp_path):
    spec = AgentSpec(name="sleeper", argv=[sys.executable, "-c", "import time; time.sleep(5)"])

    result = run_agent(spec, "prompt", tmp_path, timeout=0.5)

    assert result.timed_out is True
    assert result.ok is False
    assert "timeout" in result.evidence


def test_prompt_carries_the_contract_and_the_previous_failure(tmp_path):
    brief = {
        "task": {
            "id": "t-1",
            "title": "Add pagination",
            "description": "app.py:12",
            "acceptance": ["Page size is 20"],
            "attempts": 2,
            "max_attempts": 3,
            "notes": "attempt 2: import error",
        },
        "research": {"queries": ["pagination best practice"], "note_path": ".altai/research/t-1.md"},
        "related_files": ["app.py"],
    }

    prompt = build_prompt(brief, tmp_path)

    assert "t-1" in prompt and "Add pagination" in prompt
    assert "Page size is 20" in prompt
    assert "app.py" in prompt
    assert "attempt 2: import error" in prompt
    # The runner records outcomes; an agent that also records them double-counts.
    assert "Do NOT run `altai done`" in prompt


def test_the_prompt_is_kept_out_of_evidence_lines(tmp_path):
    spec = AgentSpec(name="echo", argv=[sys.executable, "-c", "pass"])

    result = run_agent(spec, "a very long prompt " * 200, tmp_path, timeout=30)

    # Evidence goes into commit messages and .altai/evidence/; a 4 KB prompt in
    # every line makes both unreadable.
    assert "<task prompt>" in result.evidence
    assert "a very long prompt" not in result.evidence
