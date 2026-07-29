from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .autopilot import run_autopilot
from .graph import project_phase
from .intelligence.opportunity_finder import load_opportunities
from .intelligence.project_memory import CATEGORY_FILES
from .orchestrator import (
    add_rule,
    add_task,
    block_task,
    bootstrap,
    complete_task,
    fail_attempt,
    learn,
    load_or_fail,
    next_brief,
    promote_opportunity,
    skip_task,
    status_text,
    unblock_task,
    write_agent_task,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="altai", description="ALTAI project completion agent")
    parser.add_argument("--version", action="version", version=f"altai {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_path(sub_parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub_parser.add_argument("--path", "-C", default=".", help="Project root (default: .)")
        return sub_parser

    start = sub.add_parser("start", help="Scan the project and merge findings into saved state")
    start.add_argument("path", nargs="?", default=".")

    status = with_path(sub.add_parser("status", help="Show saved state without rescanning"))
    status.add_argument("--rescan", action="store_true", help="Rescan before reporting")
    status.add_argument("--json", action="store_true", help="Emit machine-readable state")

    nxt = with_path(sub.add_parser("next", help="Show the active task and its research brief"))
    nxt.add_argument("--json", action="store_true")

    done = with_path(sub.add_parser("done", help="Mark a task complete (evidence required)"))
    done.add_argument("task_id")
    done.add_argument("--evidence", "-e", action="append", default=[], required=True)

    fail = with_path(sub.add_parser("fail", help="Record a failed attempt on a task"))
    fail.add_argument("task_id")
    fail.add_argument("--reason", "-r", default="")

    block = with_path(sub.add_parser("block", help="Block a task and record why"))
    block.add_argument("task_id")
    block.add_argument("--reason", "-r", required=True)

    unblock = with_path(sub.add_parser("unblock", help="Clear a block and reset attempts"))
    unblock.add_argument("task_id")

    skip = with_path(sub.add_parser("skip", help="Settle a task as deliberately not done"))
    skip.add_argument("task_id")
    skip.add_argument("--reason", "-r", required=True)

    add = with_path(sub.add_parser("add", help="Add a task by hand"))
    add.add_argument("title")
    add.add_argument("--id", dest="task_id", default=None)
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument("--acceptance", action="append", default=[])
    add.add_argument("--description", default="")

    learn_p = with_path(sub.add_parser("learn", help="Record a project-memory note"))
    learn_p.add_argument("category", choices=sorted(CATEGORY_FILES))
    learn_p.add_argument("note")
    learn_p.add_argument("--task-id", default="")

    rule = with_path(sub.add_parser("rule", help="Record a check-before-acting rule"))
    rule.add_argument("condition")
    rule.add_argument("rule")
    rule.add_argument("--task-id", default="")

    opportunities = with_path(
        sub.add_parser("opportunities", help="List scored, not-yet-adopted improvement candidates")
    )
    opportunities.add_argument("--json", action="store_true")

    promote = with_path(
        sub.add_parser("promote", help="Turn one opportunity into a real task")
    )
    promote.add_argument("opportunity_id")

    autopilot = with_path(
        sub.add_parser(
            "autopilot",
            help="Rescan once and report the single next actionable unit, opportunities and "
            "a policy check. Does not implement anything itself.",
        )
    )
    autopilot.add_argument("project", nargs="?", help="Project root (alternative to --path)")
    autopilot.add_argument(
        "--design",
        action="store_true",
        help="Generate product architecture, UX, screens, tokens, and a UI review first",
    )
    autopilot.add_argument("--json", action="store_true")
    autopilot.add_argument("--no-rescan", action="store_true")
    autopilot.add_argument(
        "--apply-recommendations",
        action="store_true",
        help="Turn safe improvement recommendations into tasks for the host agent to apply",
    )

    return parser


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "project", None) or getattr(args, "path", ".")).resolve()


def _cmd_start(args: argparse.Namespace) -> int:
    state = bootstrap(_root(args), rescan=True)
    print(status_text(state))
    print("\nAjan komutu: .altai/AGENT_TASK.md dosyasindaki dongüyu uygula.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if args.rescan:
        state = bootstrap(_root(args), rescan=True)
    else:
        state = load_or_fail(_root(args))
    if args.json:
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(status_text(state))
    return 0


#: Exit codes so a shell loop can branch without parsing the status text.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 3
EXIT_COMPLETE = 4


def _cmd_next(args: argparse.Namespace) -> int:
    state = load_or_fail(_root(args))
    brief = next_brief(state)
    if brief is None:
        phase = project_phase(state.tasks)
        if args.json:
            # Stay machine-readable on every path, not just the happy one.
            print(
                json.dumps(
                    {
                        "task": None,
                        "phase": phase,
                        "blocked": [
                            {"id": t.id, "reason": t.blocked_reason}
                            for t in state.tasks
                            if t.status.value == "blocked"
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(status_text(state))
        return EXIT_COMPLETE if phase == "DONE" else EXIT_BLOCKED
    if args.json:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        return 0
    task = brief["task"]
    print(f"Gorev: {task['id']} - {task['title']}")
    if task["description"]:
        print(f"Kaynak: {task['description']}")
    print(f"Deneme: {task['attempts']}/{task['max_attempts']}")
    if task["acceptance"]:
        print("Kabul:")
        for item in task["acceptance"]:
            print(f"  - {item}")
    research = brief["research"]
    print("Ara:")
    for query in research["queries"]:
        print(f"  - {query}")
    print("Oncelik: " + ", ".join(research["preferred_domains"][:5]))
    print(f"Not: {research['note_path']}")
    if brief.get("related_files"):
        print("Ilgili dosyalar: " + ", ".join(brief["related_files"]))
    if brief.get("memory"):
        print("Proje hafizasi:")
        for line in brief["memory"].splitlines():
            print(f"  {line}")
    return 0


def _cmd_done(args: argparse.Namespace) -> int:
    state = complete_task(_root(args), args.task_id, args.evidence)
    print(status_text(state))
    return 0


def _cmd_fail(args: argparse.Namespace) -> int:
    state = fail_attempt(_root(args), args.task_id, args.reason)
    print(status_text(state))
    return 0


def _cmd_block(args: argparse.Namespace) -> int:
    state = block_task(_root(args), args.task_id, args.reason)
    print(status_text(state))
    return 0


def _cmd_unblock(args: argparse.Namespace) -> int:
    state = unblock_task(_root(args), args.task_id)
    print(status_text(state))
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    state = skip_task(_root(args), args.task_id, args.reason)
    print(status_text(state))
    return EXIT_OK


def _cmd_add(args: argparse.Namespace) -> int:
    state, task = add_task(
        _root(args),
        title=args.title,
        task_id=args.task_id,
        depends_on=args.depends_on,
        acceptance=args.acceptance,
        description=args.description,
    )
    write_agent_task(state)
    print(f"Eklendi: {task.id}")
    print(status_text(state))
    return 0


def _cmd_learn(args: argparse.Namespace) -> int:
    path = learn(_root(args), args.category, args.note, task_id=args.task_id)
    print(f"Kaydedildi: {path}")
    return 0


def _cmd_rule(args: argparse.Namespace) -> int:
    path = add_rule(_root(args), args.condition, args.rule, task_id=args.task_id)
    print(f"Kaydedildi: {path}")
    return 0


def _cmd_opportunities(args: argparse.Namespace) -> int:
    candidates = load_opportunities(_root(args))
    if args.json:
        print(json.dumps([c.to_dict() for c in candidates], ensure_ascii=False, indent=2))
        return 0
    if not candidates:
        print("Firsat yok.")
        return 0
    for candidate in candidates:
        print(f"{candidate.id}  [{candidate.score:+.1f}]  {candidate.title}")
        print(f"  {candidate.file}")
    print("\nGercek gorev haline getirmek icin: altai promote <id>")
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    state, task = promote_opportunity(_root(args), args.opportunity_id)
    write_agent_task(state)
    print(f"Eklendi: {task.id}")
    print(status_text(state))
    return 0


def _cmd_autopilot(args: argparse.Namespace) -> int:
    report = run_autopilot(
        _root(args),
        rescan=not args.no_rescan,
        design=args.design,
        apply_recommendations=args.apply_recommendations,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return report.exit_code
    print(f"Faz: {report.phase}")
    if report.task is not None:
        task = report.task["task"]
        print(f"Gorev: {task['id']} - {task['title']}")
        if report.policy_flags:
            print("Politika bayraklari: " + ", ".join(report.policy_flags))
    for item in report.blocked[:3]:
        print(f"Blok: {item['id']} - {item['reason'] or 'sebep kayitli degil'}")
    if report.applied_recommendations:
        print("Otomatik goreve alinan oneriler:")
        for candidate in report.applied_recommendations:
            print(f"  {candidate['id']}  {candidate['title']}")
    if report.opportunities:
        print("Firsatlar:")
        for candidate in report.opportunities:
            print(f"  {candidate['id']}  [{candidate['score']:+.1f}]  {candidate['title']}")
    if report.design:
        print("Tasarim:")
        for name, path in report.design.items():
            print(f"  {name}: {path}")
    print(f"Talimat: {report.instruction}")
    return report.exit_code


HANDLERS = {
    "start": _cmd_start,
    "status": _cmd_status,
    "next": _cmd_next,
    "done": _cmd_done,
    "fail": _cmd_fail,
    "block": _cmd_block,
    "unblock": _cmd_unblock,
    "skip": _cmd_skip,
    "add": _cmd_add,
    "learn": _cmd_learn,
    "rule": _cmd_rule,
    "opportunities": _cmd_opportunities,
    "promote": _cmd_promote,
    "autopilot": _cmd_autopilot,
}


def main(argv: list[str] | None = None) -> int:
    """Always returns an exit code.

    Argparse raises SystemExit on a usage error, and the filesystem raises the
    wider OSError family (PermissionError, and on Windows a locked file during
    os.replace). Letting either escape hands an agent loop a traceback instead
    of the `Hata:` line it parses.
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        return HANDLERS[args.command](args)
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Hata: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
