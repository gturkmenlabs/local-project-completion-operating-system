from altai.models import Task
from altai.policy_engine import CATEGORY_KEYWORDS, classify, flags_for_task


def test_classify_matches_known_category():
    assert "destructive" in classify("drop table users")
    assert "credentials" in classify("store the api key in a config file")
    assert "spending" in classify("add a checkout flow with billing")
    assert "publish" in classify("deploy to production")
    assert "irreversible-product-decision" in classify("this is a breaking change")


def test_classify_returns_empty_for_routine_text():
    assert classify("fix the login redirect bug") == []


def test_classify_can_match_multiple_categories():
    flags = classify("delete the user's stored password before deployment")
    assert "destructive" in flags
    assert "credentials" in flags
    assert "publish" in flags


def test_every_category_has_at_least_one_keyword():
    for category, keywords in CATEGORY_KEYWORDS.items():
        assert keywords, f"{category} has no keywords"


def test_flags_for_task_checks_title_description_and_acceptance():
    task = Task(id="t", title="Routine change", description="", acceptance=["remove all rows first"])
    assert "destructive" in flags_for_task(task)


def test_flags_for_task_empty_for_ordinary_task():
    task = Task(id="t", title="Fix off-by-one error", description="in the pagination code")
    assert flags_for_task(task) == []
