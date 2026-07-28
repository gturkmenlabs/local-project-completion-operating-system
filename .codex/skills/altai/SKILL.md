---
name: altai
description: Autonomously inspect, research, implement, test, and finish the current software project with minimal user prompting. Use when the user asks to continue, finish, repair, or complete the project.
---

# ALTAI

Use this skill when the user asks to finish, continue, repair, complete, or autonomously
develop the current project.

## Command

Prefer the vendored launcher; fall back to the installed console script.

```bash
python .altai/tool/run.py status   # if .altai/tool/run.py exists
altai status                       # if the package was pip-installed
```

If `.altai/project-state.json` does not exist, initialize first:

```bash
python .altai/tool/run.py start .
```

Then read `.altai/AGENT_TASK.md`.

## Mandatory loop

1. `... next` — returns exactly one dependency-ready task plus a research brief.
   Do not choose a task yourself; the dependency graph already did.
2. Research only what that task needs, using your own web search. Prefer the domains
   listed in the brief. Never fetch a search-engine results page.
3. Save a compressed note to `.altai/research/<task-id>.md`: URL, date, pattern,
   compatibility risk, decision.
4. Restate the acceptance criteria before editing.
5. Implement the smallest complete change.
6. Run tests and quality gates.
7. Record the outcome through the CLI — never by editing the JSON:
   - success: `... done <task-id> --evidence "pytest -> 42 passed"`
   - failure: `... fail <task-id> --reason "<what broke>"`
   - hard stop: `... block <task-id> --reason "<why>"`
   - won't do: `... skip <task-id> --reason "<why>"` (only with the user's agreement)
8. Repeat until `... status` prints `Durum: BITTI`.

`next` exit codes: `0` a task was returned, `3` the project is blocked, `4` it is complete.

Evidence must be fresh and non-empty on every `done`. Evidence recorded before the change
you are verifying does not count — re-run the checks. `done` also refuses a task whose
dependencies are unfinished, and one that is currently blocked. `fail` refuses blocked and
finished tasks; it is not a way to reopen them.

Adding work or discovering new markers reopens `final-verification` and clears its old
evidence. Re-run the full verification rather than looking for a shortcut.

After three failed attempts a task is blocked automatically. Do not retry it a fourth
time with the same approach — change strategy, or escalate to the user. `unblock` works
at most twice per task; after that the CLI refuses and you must involve the user.

Watch for `Risk:` lines in `status`: they report things the plan cannot see, such as a
marker scan that hit its cap or work lost during a state migration.

`... start .` is safe to re-run at any time: it merges newly discovered TODO/FIXME work
into the plan and preserves all recorded status, attempts and evidence.

Never open a new feature while a finishable current task remains. Ask the user only for
destructive actions, secrets, paid actions, publishing, or genuinely unknowable product
decisions.

## Token mode

Short status lines. No repeated summaries. Diffs over full files. Detailed evidence lives
in `.altai/`, not in chat.
