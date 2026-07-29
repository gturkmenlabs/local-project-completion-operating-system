"""Classify a task's own text against the stop-and-ask categories `CLAUDE.md`
already names, so `autopilot` can flag one instead of handing it out as routine.

This module enforces nothing. It cannot: ALTAI ships with zero dependencies and
no sandbox of its own — it has no way to block a network call, a file write, or
a shell command the way `.claude/settings.json` permissions and hooks can. All
it does is keyword-match a task's title, description and acceptance criteria
against categories from ALTAI's own operating contract:

    "Human approval remains mandatory for destructive actions, credentials,
    spending, deployment/publication, and ambiguous product decisions."

A match is a hint the host agent should stop and ask, not a verdict — and a
category never matching is not proof the task is safe, only that its own words
did not say so. Real enforcement belongs in Claude Code / Codex configuration,
not here.
"""

from __future__ import annotations

from .models import Task

#: category -> substrings that, appearing anywhere in the task's own text,
#: suggest that category applies. Deliberately narrow: a false negative here
#: just means the host agent applies its own judgement, same as always. A
#: false positive costs one unnecessary pause, which is the safe direction to
#: err in.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "destructive": (
        "delete", "drop table", "drop database", "truncate", "rm -rf", "remove all",
        "wipe", "purge", "irreversible",
    ),
    "credentials": (
        "password", "secret", "api key", "apikey", "credential", "private key",
        ".env", "access token", "auth token",
    ),
    "spending": (
        "purchase", "buy ", "payment", "billing", "invoice", "subscription",
        "credit card", "checkout",
    ),
    "publish": (
        "deploy", "deployment", "publish", "release to production", "push to prod",
        "npm publish", "app store", "go live",
    ),
    "irreversible-product-decision": (
        "breaking change", "remove feature", "drop support", "rename public api",
        "change pricing", "delete user data",
    ),
}


def classify(text: str) -> list[str]:
    """Categories whose keywords appear in *text*, in ``CATEGORY_KEYWORDS`` order."""
    folded = text.lower()
    return [category for category, keywords in CATEGORY_KEYWORDS.items() if any(k in folded for k in keywords)]


def flags_for_task(task: Task) -> list[str]:
    text = f"{task.title} {task.description} {' '.join(task.acceptance)}"
    return classify(text)
