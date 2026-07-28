from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Authoritative sources per detected stack, ranked. The host agent should try
#: these before anything else it finds.
PREFERRED_DOMAINS = {
    "Python": ["docs.python.org", "packaging.python.org", "peps.python.org"],
    "Node.js": ["nodejs.org/docs", "developer.mozilla.org"],
    "TypeScript": ["typescriptlang.org/docs", "developer.mozilla.org"],
    "Rust": ["doc.rust-lang.org", "docs.rs"],
    "Go": ["go.dev/doc", "pkg.go.dev"],
    "Java": ["docs.oracle.com/en/java", "openjdk.org"],
    "Ruby": ["ruby-doc.org", "guides.rubyonrails.org"],
    "PHP": ["php.net/docs.php"],
    "Docker": ["docs.docker.com"],
}

GENERIC_DOMAINS = ["owasp.org", "developer.mozilla.org"]


@dataclass(slots=True)
class ResearchBrief:
    """Instructions for the *host* agent's web tools.

    ALTAI deliberately does not fetch anything itself. It also does not hand out
    ``google.com/search`` URLs: search-engine result pages are not fetchable by
    agent tooling, so a link like that is dead weight. Instead it emits plain
    query strings to feed into the host's own web search.
    """

    task_id: str
    queries: list[str]
    preferred_domains: list[str]
    instructions: str
    note_path: str
    search_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "queries": self.queries,
            "preferred_domains": self.preferred_domains,
            "instructions": self.instructions,
            "note_path": self.note_path,
        }

    def as_text(self) -> str:
        lines = [f"Task: {self.task_id}", "Queries:"]
        lines += [f"  - {query}" for query in self.queries]
        lines.append("Prefer: " + ", ".join(self.preferred_domains))
        lines.append(f"Save note to: {self.note_path}")
        lines.append(self.instructions)
        return "\n".join(lines)


def preferred_domains_for(stack: list[str]) -> list[str]:
    domains: list[str] = []
    for item in stack:
        domains.extend(PREFERRED_DOMAINS.get(item, []))
    domains.extend(GENERIC_DOMAINS)
    return list(dict.fromkeys(domains))


def build_research_brief(
    project_root: Path,
    task_title: str,
    stack: list[str],
    task_id: str = "task",
) -> ResearchBrief:
    stack_text = " ".join(stack) or "software project"
    queries = [
        f"{stack_text} {task_title} official documentation",
        f"{stack_text} {task_title} best practices",
        f"{task_title} example implementation site:github.com",
    ]
    instructions = (
        "Use the host agent's own web search. Prefer official documentation and maintained "
        "upstream repositories over blog posts. For each source record: URL, publication or "
        "last-updated date, the concrete pattern used, compatibility risk against this project's "
        "versions, and the decision taken. Never execute code copied from an untrusted page "
        "before reading it."
    )
    return ResearchBrief(
        task_id=task_id,
        queries=queries,
        preferred_domains=preferred_domains_for(stack),
        instructions=instructions,
        note_path=f".altai/research/{task_id}.md",
    )
