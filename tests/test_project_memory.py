import json
import threading

import pytest

from altai.intelligence.project_memory import (
    CATEGORY_FILES,
    category_path,
    digest,
    has_content,
    init_memory,
    load_rules,
    memory_dir,
    record,
    record_rule,
    recent_entries,
    rules_path,
)
from altai.orchestrator import add_rule, learn


def test_init_memory_creates_every_category_file(tmp_path):
    init_memory(tmp_path)

    for filename in CATEGORY_FILES.values():
        assert (memory_dir(tmp_path) / filename).exists()
    assert rules_path(tmp_path).exists()
    assert json.loads(rules_path(tmp_path).read_text(encoding="utf-8")) == []


def test_init_memory_does_not_clobber_existing_content(tmp_path):
    init_memory(tmp_path)
    path = category_path(tmp_path, "architecture")
    path.write_text(path.read_text(encoding="utf-8") + "- hand written\n", encoding="utf-8")

    init_memory(tmp_path)

    assert "hand written" in path.read_text(encoding="utf-8")


def test_record_appends_timestamped_entry(tmp_path):
    record(tmp_path, "failed-approaches", "tried global session state, broke concurrency")

    text = category_path(tmp_path, "failed-approaches").read_text(encoding="utf-8")
    assert "tried global session state, broke concurrency" in text


def test_record_rejects_unknown_category(tmp_path):
    with pytest.raises(ValueError, match="Unknown memory category"):
        record(tmp_path, "nonsense", "x")


def test_record_rejects_empty_note(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        record(tmp_path, "architecture", "   ")


def test_record_rule_persists_structured_json(tmp_path):
    record_rule(tmp_path, "editing auth code", "check .altai/memory/failed-approaches.md first")

    rules = load_rules(tmp_path)
    assert len(rules) == 1
    assert rules[0]["condition"] == "editing auth code"
    assert rules[0]["rule"] == "check .altai/memory/failed-approaches.md first"
    assert "learned_at" in rules[0]


def test_has_content_false_until_something_is_recorded(tmp_path):
    assert has_content(tmp_path) is False
    record(tmp_path, "architecture", "orchestrator owns final decisions")
    assert has_content(tmp_path) is True


def test_digest_omits_empty_categories(tmp_path):
    record(tmp_path, "architecture", "orchestrator owns final decisions")

    text = digest(tmp_path)

    assert "architecture:" in text
    assert "product-decisions:" not in text


def test_digest_includes_recent_rules(tmp_path):
    record_rule(tmp_path, "touching billing", "get explicit sign-off first")

    text = digest(tmp_path)

    assert "touching billing" in text
    assert "get explicit sign-off first" in text


def test_recent_entries_respects_limit(tmp_path):
    for i in range(5):
        record(tmp_path, "coding-conventions", f"convention {i}")

    entries = recent_entries(tmp_path, "coding-conventions", limit=2)

    assert len(entries) == 2
    assert "convention 4" in entries[-1]


def test_orchestrator_learn_and_add_rule_wrappers(tmp_path):
    path = learn(tmp_path, "user-preferences", "user rejected auto-deploy", task_id="t1")
    assert "[t1]" in path.read_text(encoding="utf-8")

    add_rule(tmp_path, "deploy suggested", "ask the user first", task_id="t1")
    rules = load_rules(tmp_path)
    assert rules[0]["task_id"] == "t1"


def test_concurrent_record_rule_does_not_lose_an_entry(tmp_path):
    """Regression for a lost-update race: record_rule used to read
    learned-rules.json, append in memory, and write it back with no lock.
    Two concurrent calls could both read the same list before either wrote
    it back; the second write silently discarded the first call's rule even
    though the file stayed valid JSON — no visible error, just missing data."""
    count = 8
    errors = []
    barrier = threading.Barrier(count)

    def _record(i):
        try:
            barrier.wait(timeout=5)
            record_rule(tmp_path, f"condition {i}", f"rule {i}")
        except Exception as error:  # noqa: BLE001 - surfaced via `errors` below
            errors.append(error)

    threads = [threading.Thread(target=_record, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    rules = load_rules(tmp_path)
    assert {r["condition"] for r in rules} == {f"condition {i}" for i in range(count)}
