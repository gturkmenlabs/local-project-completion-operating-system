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

1. `... next` — returns exactly one dependency-ready task plus a research brief. It may also
   include `related_files` (from `.altai/code-graph.json`, best-guess by symbol/word match) and
   `memory` (a digest of `.altai/memory/`, see below). Do not choose a task yourself; the
   dependency graph already did.
   - A task whose id starts with `gap-` was not found by a marker scan — it is a contradiction
     the gap analyzer found between what `.altai/project-model.json` declares and what the
     repository actually has (missing tests, no run command, unconfirmed purpose). Resolve the
     stated contradiction; delegate `gap-confirm-project-model` to the `altai-analyst` subagent
     if one is available.
2. Research only what that task needs, using your own web search. Prefer the domains
   listed in the brief. Never fetch a search-engine results page.
3. Save a compressed note to `.altai/research/<task-id>.md`: URL, date, pattern,
   compatibility risk, decision.
4. Restate the acceptance criteria before editing.
5. For a multi-file task, consult `related_files` and `.altai/code-graph.json` (or delegate to
   the `altai-architect` subagent) before touching code. Skip this for a one-file change.
6. Implement the smallest complete change.
7. Run tests and quality gates.
8. Record the outcome through the CLI — never by editing the JSON:
   - success: `... done <task-id> --evidence "pytest -> 42 passed"`
   - failure: `... fail <task-id> --reason "<what broke>"`
   - hard stop: `... block <task-id> --reason "<why>"`
   - won't do: `... skip <task-id> --reason "<why>"` (only with the user's agreement)
9. If the task revealed a durable decision, a rejected approach, or a convention the repository
   does not otherwise document, record it: `... learn <category> "<note>"` (categories:
   `architecture`, `product-decisions`, `coding-conventions`, `failed-approaches`,
   `user-preferences`) or `... rule "<condition>" "<rule>"` for a check-before-acting rule. Skip
   this when there is nothing worth a future task reading — not every task produces one.
10. Repeat until `... status` prints `Durum: BITTI`.

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

## Opportunities and autopilot

`... opportunities` lists scored candidates nobody asked for by name — a large function, a
name duplicated across files, a heavily-called function with no test file — derived from
`.altai/code-graph.json` and `.altai/project-model.json`, never from web research. A gap
task (`gap-*`) closes a contradiction in what the repository already declares; an
opportunity *creates* new intent, so it is never added to the task graph automatically.
Nothing happens to one until you run `... promote <opportunity-id>`, which turns it into a
normal task (still gated by quality-gates, still counted by `final-verification`, like any
`add`ed task). Do not promote one without the user's agreement unless it is an obvious,
low-risk improvement directly serving the declared purpose.

`... autopilot` runs one rescan and reports, in a single call, the next ready task (with
`related_files` and `memory`), the top open opportunities, and a policy check on the active
task's own text against `CLAUDE.md`'s stop-and-ask categories (destructive, credentials,
spending, publish, irreversible product decision). It does not implement anything — the
loop above still applies to whatever task it returns. Exit code `5` means the returned
task's own text matched a policy category: stop and get the user's agreement before
proceeding, the same as any destructive/credential/spending/publish/product decision.
Calling `autopilot` repeatedly is fine — each call is one bounded rescan, not a loop that
keeps scanning internally.

## Token mode

Short status lines. No repeated summaries. Diffs over full files. Detailed evidence lives
in `.altai/`, not in chat.
