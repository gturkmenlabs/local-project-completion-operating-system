from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .autonomy import FULL, GUARDED, Autonomy
from .autopilot import run_autopilot
from .graph import project_phase
from .loop import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_SWEEPS,
    run_project,
)
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

    run = with_path(
        sub.add_parser(
            "run",
            help="Single command: scan, plan, apply recommendations, implement, verify and "
            "record every task until the project is done.",
        )
    )
    run.add_argument("project", nargs="?", help="Project root (alternative to --path)")
    run.add_argument(
        "--autonomy",
        choices=[FULL, GUARDED],
        default=None,
        help=f"{FULL} (default): approve and apply everything unattended. "
        f"{GUARDED}: stop on stop-and-ask categories. Overrides ALTAI_AUTONOMY.",
    )
    run.add_argument(
        "--safe",
        dest="autonomy",
        action="store_const",
        const=GUARDED,
        help=f"Shorthand for --autonomy {GUARDED}",
    )
    run.add_argument(
        "--plan-only",
        action="store_true",
        help="Report the next task without launching an agent (the caller implements it)",
    )
    run.add_argument(
        "--design",
        dest="design",
        action="store_const",
        const=True,
        default=None,
        help="Force the pre-code design plan (attempted by default; skipped with a note "
        "while the project model is unconfirmed)",
    )
    run.add_argument(
        "--no-design",
        dest="design",
        action="store_const",
        const=False,
        help="Skip the design pass entirely",
    )
    run.add_argument(
        "--commit",
        dest="commit",
        action="store_const",
        const=True,
        default=None,
        help="Commit each completed task (default: on when the working tree is clean)",
    )
    run.add_argument(
        "--no-commit", dest="commit", action="store_const", const=False, help="Never commit"
    )
    run.add_argument(
        "--no-rollback",
        dest="rollback",
        action="store_const",
        const=False,
        default=None,
        help="Keep a failed attempt's changes instead of resetting to the last checkpoint",
    )
    run.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Per-task ceiling on the agent's own turns where its CLI supports one (0 = none)",
    )
    run.add_argument(
        "--no-apply",
        dest="apply_recommendations",
        action="store_false",
        help="Do not promote improvement recommendations into tasks",
    )
    run.add_argument("--no-rescan", action="store_true")
    run.add_argument(
        "--agent",
        default=None,
        help="Host agent command: a name (claude, codex), a full command line "
        "(optionally containing {prompt}), or 'none' for plan-only. "
        "Defaults to ALTAI_AGENT_CMD, then the first CLI found on PATH.",
    )
    run.add_argument(
        "--check",
        dest="checks",
        action="append",
        default=[],
        help="Extra verification command to run after every task (repeatable)",
    )
    run.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    run.add_argument("--max-sweeps", type=int, default=DEFAULT_MAX_SWEEPS)
    run.add_argument("--agent-timeout", type=float, default=None, help="Seconds per task")
    run.add_argument("--check-timeout", type=float, default=None, help="Seconds per check")
    run.add_argument("--time-budget", type=float, default=None, help="Seconds for the whole run")
    run.add_argument(
        "--allow-nested",
        action="store_true",
        help="Spawn an agent even when already running inside one",
    )
    run.add_argument("--json", action="store_true")

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


def _cmd_run(args: argparse.Namespace) -> int:
    kwargs: dict[str, object] = {}
    # Only forward the timeouts the caller actually set, so the executor's own
    # defaults stay in one place.
    if args.agent_timeout is not None:
        kwargs["agent_timeout"] = args.agent_timeout
    if args.check_timeout is not None:
        kwargs["check_timeout"] = args.check_timeout
    if args.max_turns is not None:
        kwargs["max_turns"] = args.max_turns

    report = run_project(
        _root(args),
        autonomy=Autonomy.from_env(args.autonomy),
        plan_only=args.plan_only,
        design=args.design,
        apply_recommendations=args.apply_recommendations,
        rescan=not args.no_rescan,
        agent=args.agent,
        checks=args.checks,
        commit=args.commit,
        rollback=args.rollback,
        max_iterations=args.max_iterations,
        max_sweeps=args.max_sweeps,
        time_budget=args.time_budget,
        allow_nested=args.allow_nested,
        **kwargs,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return report.exit_code

    agent = report.plan.get("agent")
    print(f"Otonomi: {report.autonomy}")
    print(f"Ajan: {agent['name'] if agent else 'yok (plan-only)'}")
    if report.plan.get("checks"):
        print("Dogrulama: " + ", ".join(check["command"] for check in report.plan["checks"]))
    if report.applied_recommendations:
        print("Otomatik uygulanan oneriler:")
        for candidate in report.applied_recommendations:
            print(f"  {candidate['id']}  {candidate['title']}")
    for iteration in report.iterations:
        mark = {"done": "OK", "failed": "HATA", "held": "BEKLEME", "handoff": "DEVIR"}
        line = f"[{mark.get(iteration.outcome, iteration.outcome)}] {iteration.task_id} - {iteration.title}"
        if iteration.auto_approved:
            line += f"  (otomatik onay: {', '.join(iteration.policy_flags)})"
        if iteration.commit:
            line += f"  commit {iteration.commit[:9]}"
        if iteration.rolled_back:
            line += "  (geri alindi)"
        print(line)
        if iteration.reason:
            print(f"      {iteration.reason}")
    for note in report.notes:
        print(f"Not: {note}")
    for item in report.blocked[:3]:
        print(f"Blok: {item['id']} - {item['reason'] or 'sebep kayitli degil'}")
    if report.design:
        print("Tasarim:")
        for name, path in report.design.items():
            print(f"  {name}: {path}")
    summary = f"Faz: {report.phase}  Biten: {len(report.completed)}  Sure: {report.duration:.0f}s"
    if report.commits:
        summary += f"  Commit: {len(report.commits)}"
    if report.cost_usd is not None:
        summary += f"  Maliyet: ${report.cost_usd:.2f}"
    print(summary)
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
    "run": _cmd_run,
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
