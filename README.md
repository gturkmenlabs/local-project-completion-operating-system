# ALTAI

ALTAI is a local project-completion operating system for Claude Code and Codex. It scans a
repository, builds a model of what the project is *for*, forms a task graph, prepares
task-specific research instructions — and then runs the whole thing to completion:

```bash
altai run
```

One command takes a repository from wherever it is to done. It hands each dependency-ready
task to your host agent CLI, verifies the result against the project's own checks, commits
what passed, rolls back what failed, and repeats. Unattended by default.

Requires Python 3.10+. No dependencies.

## Install into a project

```bash
python scripts/install_into_project.py /path/to/your-project
cd /path/to/your-project
python .altai/tool/run.py run .
```

The installer vendors the package under `.altai/tool/` — nothing lands in your project
root except the agent config files. An existing `CLAUDE.md` or `AGENTS.md` is preserved:
the ALTAI section is spliced in between `<!-- BEGIN ALTAI -->` / `<!-- END ALTAI -->`
markers and re-running the installer updates that block in place.

Install the same project-local stack into several projects in one command:

```bash
python scripts/install_into_project.py ~/code/app-one ~/code/app-two
```

The default bundle contains ALTAI, the product-design layer, and Caveman. Preview without
writing, or omit Caveman for new targets:

```bash
python scripts/install_into_project.py --dry-run ~/code/app-one ~/code/app-two
python scripts/install_into_project.py --no-caveman ~/code/app-one
```

Each target receives `.altai/integration.json`, which records installed features and the
exact commands for that project (`start`, `run`, `continue`, `design`, `safe`). Re-running
the installer updates ALTAI-owned files while preserving existing `AGENTS.md` and
`CLAUDE.md` content.

Optionally install the CLI globally instead of vendoring it:

```bash
pip install -e .
altai run /path/to/your-project
```

## Use it: one command

```bash
altai run              # or: python .altai/tool/run.py run .
```

That is the whole operating loop, unattended. `run` rescans the repository, promotes its
own improvement recommendations into real work, hands each dependency-ready task to your
host agent CLI (`claude` or `codex`), re-runs the project's declared checks itself, records
`done` or `fail` with evidence, sweeps for work the change just created, and repeats until
the project is done, blocked, or the run's budget is spent. Nothing needs a prompt per
task.

```bash
altai run --safe                    # keep the stop-and-ask holds (see Autonomy)
altai run --plan-only               # next task only, implement it yourself
altai run --check "npm run e2e"     # add a gate every task must pass
altai run --no-commit               # do not commit per task
altai run --agent codex --max-iterations 50 --time-budget 3600
```

Alternatively, open the project in Claude Code or Codex and say `Continue.` — the host
agent reads `CLAUDE.md` / `AGENTS.md`, loads the ALTAI skill, and drives the same loop
itself. Inside a host agent, `altai run` detects the nesting and hands the task to its
caller instead of spawning a second agent underneath it.

### What the runner does per task

1. `next` picks the single dependency-ready task — the dependency graph decides, not the agent.
2. One headless agent invocation gets the task, its acceptance criteria, its research
   queries, the likely files from the code graph, project memory, and the previous
   attempt's recorded cause.
3. The **runner** — not the agent — runs the project's verification commands from
   `project-model.json` (`test`, `build`, `lint`, `typecheck`, `check`) plus any `--check`.
4. Green agent *and* green gates: `done` with the commands and their exit codes as
   evidence. Anything else: `fail` with the cause. Three failures block the task, exactly
   as they do when a human drives the loop.

A project that declares no test or build command says so in the report: with no gate,
completion rests on the agent's exit code alone, which is the weakest evidence this tool
accepts.

Before any of that, the run writes the pre-code design plan (`.altai/design/`) whenever the
project model is confirmed — `--no-design` skips it, and an unconfirmed model skips it with
a note instead of an error.

### Checkpoints: the undo an unattended run needs

Each completed task becomes its own commit (`altai(<task-id>): <title>`, with the
verification commands and their exit codes in the body). Each failed attempt is reset back
to the previous checkpoint, so the next attempt starts from a clean tree instead of
someone's half-finished one. `.altai/` is ignored by the reset, so the state, evidence and
audit trail of the run survive every rollback.

Both are refused outright when the working tree is dirty at the start of the run: a commit
would bury uncommitted work and a reset would delete it. The run says so in its notes and
continues without checkpoints. `--commit` forces commits anyway (rollback stays off),
`--no-commit` disables them, `--no-rollback` keeps a failed attempt in the tree for
inspection.

### Budget

`--max-turns` (default 120, `0` removes it) caps the agent's own turns per task where its
CLI supports one, `--agent-timeout` caps its wall clock, `--max-iterations` caps tasks per
run, and `--time-budget` caps the whole run. Spending a budget is not a failure: exit code
7 means "stopped with work still ready", and re-running continues from exactly there. When
the agent reports its own cost — Claude Code's headless JSON does — the run reports the
per-task and total spend.

### Autonomy

`altai run` is unattended by default (`--autonomy full`, or `ALTAI_AUTONOMY=full`):

* a task whose own text matches a stop-and-ask category is approved automatically instead
  of holding the run,
* every scored recommendation is promoted into work, flagged ones included,
* the host agent CLI is launched with its own approval prompts disabled
  (`--permission-mode bypassPermissions` for `claude`,
  `--dangerously-bypass-approvals-and-sandbox` for `codex`).

Every automatic approval is written to `.altai/runs/log.md` and to the task's evidence
file, so removing the pause does not remove the record. `--safe` (`--autonomy guarded`)
restores the previous behaviour: flagged tasks stop the run with exit code 5 and flagged
recommendations are left pending.

Full autonomy grants the agent nothing your own CLI and account do not already allow, and
ALTAI is not a sandbox (see [Safety](#safety)). Run it on a repository whose worst case is
a bad commit: version-controlled, no production credentials in the working tree, no
deploy-on-push.

## Project intelligence layer

Beyond the task graph, ALTAI keeps a model of the project itself under `.altai/`:

* **`project-model.json`** — the project's declared purpose, audience, core flow and
  non-goals, extracted from README/docs/manifests and confirmed by the host agent.
* **`code-graph.json`** — a file → class → function → call graph (`ast` for Python, a
  regex pass for other languages), used to guess which files a task likely touches.
* **`.altai/memory/`** — five category files (architecture, product-decisions,
  coding-conventions, failed-approaches, user-preferences) plus a structured
  `learned-rules.json`, written by the host agent via `altai learn` / `altai rule` — and by
  `altai run` whenever it adopts a recommendation — then re-surfaced in every later task's
  brief. Nothing here is inferred from a diff: every entry is a decision someone took.
* **`opportunities.json`** — scored improvement candidates the repository never named by
  ID (an oversized function, a name duplicated across files, a heavily-called function no
  test appears to cover). A candidate *creates* new intent, unlike a gap, which only closes
  a contradiction in intent the repository already declared, so nothing adopts one by
  accident: `altai promote <id>` is the deliberate manual path, and `altai run` adopts them
  as a decision it records — each promotion writes a `product-decisions` memory entry *and*
  creates the task in the same pass, so a recommendation is never filed away unapplied, nor
  applied without an explanation the next task can read. `--no-apply` turns that off, and
  `--safe` leaves candidates matching a stop-and-ask category pending.

A **benchmark task** (`benchmark-competitors`) is part of every plan, right after
`research-project`. The task graph can only close contradictions the repository already
declares — nothing in a repository says what comparable finished products do better — so
this is the one scaffold task whose answer comes from outside. Its brief is built from the
project's own confirmed purpose rather than from the task title, and its acceptance
criteria are deliberately about adoption, not documentation: at least three dated sources,
every finding marked adopt or reject with a reason, each adopted finding recorded with
`altai learn product-decisions` **and** added as a task in the same pass, each rejected one
recorded too so nobody researches it twice. Under `altai run` those new tasks are picked up
by the same loop that created them.

A **gap analyzer** compares what `project-model.json` declares against what the repository
actually has — an unconfirmed purpose, a declared test command with no tests, tests with no
command to run them, an entry point with no run command — and opens a `gap-*` task for each
contradiction that closes itself once resolved. Confirming the project's purpose is
exempt from (and gates) `quality-gates`: nothing else proceeds until the project's own
intent is confirmed, not merely derived from documentation.

## Product design and UX architecture

`altai run` writes the design plan on every run, as soon as the host agent has confirmed
`project-model.json` — before it is confirmed the pass is skipped with a note, not an
error. `--no-design` opts out, and `altai autopilot . --design` still runs it as a
standalone pass:

```bash
altai run              # design plan included, when the model is confirmed
altai run --no-design  # skip it
altai autopilot . --design
```

Before returning the normal dependency-ready task, ALTAI writes an inspectable pre-code
design plan:

```text
.altai/design/product-architecture.json
.altai/design/user-flows.md
.altai/design/screen-architecture.json
.altai/design/design-system.json
.altai/design/ui-review.json
.altai/research/design-benchmark.md
```

The flow is `ProjectModel → ProductArchitect → UXPlanner → ScreenGenerator →
DesignSystemBuilder → UIReviewer`. It prioritizes the confirmed target user, chooses the
smallest screen set supported by the core flow, preserves existing design tokens, and stops
when the model is unconfirmed. Logo/name changes, a replacement target audience, paid tools,
and legal or corporate brand decisions remain human decisions.

The design pass does not write application UI, browse the web, launch the project, take
screenshots, or claim visual conformance. Claude Code or Codex performs benchmark research
and implementation. Afterwards, `VisualVerifier` can validate the host agent's recorded
build, screenshot, mobile-width, console, and primary-flow evidence.

`altai autopilot` runs one bounded rescan and reports the single next actionable task
(with related files and memory attached), the top open opportunities, and a policy check
on the active task's own text against the stop-and-ask categories below. It does not
implement, research or test anything itself — that stays with the host agent under the
normal loop. `altai run` is the same pass with the loop closed: it keeps going, launches
the agent per task, and verifies the result (see [Use it](#use-it-one-command)).

## Commands

| Command | Purpose |
| --- | --- |
| `altai start .` | Scan and merge findings into saved state. Safe to re-run. |
| `altai status [--json] [--rescan]` | Report saved state. Does not rescan by default. |
| `altai next [--json]` | The single dependency-ready task plus its research brief. |
| `altai done <id> --evidence "..."` | Complete a task. Evidence is mandatory. |
| `altai fail <id> --reason "..."` | Record a failed attempt. |
| `altai block <id> --reason "..."` | Hard-stop a task. |
| `altai unblock <id>` | Clear a block and reset attempts (max 2 per task). |
| `altai skip <id> --reason "..."` | Settle a task as deliberately not done. |
| `altai add "title" [--depends-on id]` | Add a task by hand. |
| `altai learn <category> "note"` | Record a project-memory note (architecture, product-decisions, coding-conventions, failed-approaches, user-preferences). |
| `altai rule "condition" "rule"` | Record a check-before-acting rule. |
| `altai opportunities [--json]` | List scored, not-yet-adopted improvement candidates. |
| `altai promote <opportunity-id>` | Turn one opportunity into a real task. |
| `altai autopilot [path] [--design] [--json] [--no-rescan]` | Optionally generate the pre-code design plan, then report one task + opportunities + policy check. |
| `altai run [path]` | The single command: plan, apply recommendations, implement, verify and record every task until the project is done. |

`run` flags: `--safe` / `--autonomy {full,guarded}`, `--plan-only`, `--design` /
`--no-design`, `--no-apply`, `--no-rescan`, `--agent <name\|command\|none>`,
`--check <command>` (repeatable), `--commit` / `--no-commit`, `--no-rollback`,
`--max-turns`, `--max-iterations`, `--max-sweeps`, `--agent-timeout`, `--check-timeout`,
`--time-budget`, `--allow-nested`, `--json`.

Substitute `python .altai/tool/run.py` for `altai` when using the vendored install.

## Guarantees

* **Progress is never lost.** `start` merges a fresh scan into recorded state; status,
  attempts and evidence survive. Writes are atomic.
* **Task IDs are content-addressed.** Resolving one TODO does not renumber the others.
* **No silent stalls.** Exhausted attempt budgets, dependency cycles, and missing
  dependencies all produce an explicit `BLOCKED` status with a recorded reason, rather
  than an empty task queue that looks like completion.
* **Completion requires fresh evidence** on every `done` — evidence recorded before the
  change being verified does not count — plus satisfied dependencies. A blocked task
  cannot be completed, and `fail` cannot be used to launder one back into progress.
* **Final verification always covers the whole project.** Any path that leaves unfinished
  work — `add`, a rescan that finds new markers, a reverted task, a promoted opportunity —
  reopens it and discards the evidence that described the superseded state.
* **Retries are bounded.** Three failed attempts blocks a task; a task may be unblocked at
  most twice before the CLI insists on human escalation. Use `skip` to settle work that
  genuinely will not be done, so one hard block cannot make completion unreachable.
* **Parallel-safe.** Every mutation — task state, opportunity promotion, memory writes —
  takes the project's lock, so two agents cannot clobber each other's recorded work.
* **The runner verifies; the agent does not self-certify.** Under `altai run`, evidence is
  the project's own checks re-run by the runner after the agent exits. An agent that
  reports success over a red gate gets a failed attempt.
* **An unattended run is reversible.** Each completed task is its own commit and each
  failed attempt resets to the previous checkpoint — and when the working tree was already
  dirty, ALTAI refuses to commit or reset at all rather than touch work it did not write.

`next` exits 0 when it hands out a task, 3 when the project is blocked, and 4 when it is
complete. `autopilot` adds exit code 5: the returned task's own text matched a
stop-and-ask category (see Safety) and should not be implemented without the user's
agreement. `run` adds two more — 6, execution was requested but no host-agent CLI could be
resolved, and 7, the iteration or time budget was spent with work still ready (re-running
continues from exactly there). A shell loop can branch on any of these without parsing
status text.

## State layout

```text
.altai/
├── project-state.json   # task graph + progress (managed by the CLI, do not hand-edit)
├── project-model.json   # what the project is for, and where that contradicts reality
├── design/               # product architecture, flows, screens, tokens and review reports
├── code-graph.json      # file/symbol/call graph
├── opportunities.json   # scored, not-yet-adopted improvement candidates
├── memory/               # architecture, product-decisions, coding-conventions,
│                         # failed-approaches, user-preferences, learned-rules.json
├── integration.json     # installed features + this project's exact ALTAI commands
├── AGENT_TASK.md         # generated loop instructions for the host agent
├── research/             # one note per task, written by the agent
├── evidence/             # append-only evidence per task
├── runs/log.md           # audit trail of every state change
└── tool/                 # vendored ALTAI package + run.py launcher
```

## Important limit

“No API key” means ALTAI itself never calls a model API. `altai run` shells out to a host
agent CLI you already installed and signed in to (`claude`, `codex`, or whatever
`ALTAI_AGENT_CMD` names); that CLI needs its normal account or configured model access.
Web research is performed by the host agent, not by this package — ALTAI only tells it what
to look for and where to prefer looking. The same applies to `opportunities.json`: every
score is derived mechanically from the code graph and project model, never from competitor
research or a guessed "market value" — ALTAI does not fetch anything on its own.

## Safety

`altai autopilot` and `altai run --safe` keyword-check a task's own text against five
categories — destructive, credentials, spending, publish, irreversible product decision —
and flag rather than proceed when one matches (exit code 5).

`altai run` at its default `full` autonomy deliberately does not stop there: it approves
those categories, promotes flagged recommendations, and disables the host agent's own
approval prompts, because a loop that pauses for confirmation is not unattended. What it
keeps instead of the pause is the record — every automatic approval names the task and the
categories in `.altai/runs/log.md` and in `.altai/evidence/<task-id>.md` — and the
evidence contract: a task still only completes when the project's own checks pass.

What it also keeps is an undo: per-task commits and rollback-on-failure mean an unattended
run's mistakes are revertable, and it refuses to checkpoint at all rather than commit over
uncommitted work of yours.

Neither mode is enforcement. ALTAI has no sandbox: the spawned agent has exactly the
permissions its own configuration grants it, and the keyword check is a hint from a task's
own words, not a verdict on what the change will do. Real enforcement of what the host
agent can do belongs to Claude Code / Codex configuration (`.claude/settings.json`
permissions and hooks), not to this package. Use `--safe` where a wrong step is expensive,
and full autonomy where the worst case is a bad commit.

## Where the design came from

`docs/benchmark-2026-07.md` records the July 2026 research pass behind `altai run`: what
comparable autonomous coding harnesses do, which of their capabilities this project
adopted, and which it deliberately did not (worktree isolation, parallel agents,
multi-persona SDLC role-play, a second-agent review gate) with the reason for each.

## Development

```bash
python -m pytest -q
python -m compileall -q altai
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir /tmp/altai-wheel
```

These are the required local quality gates: tests, syntax compilation, and an installable
wheel. Every command must exit with status 0; any failure keeps the active ALTAI task open
until it is fixed and rerun. The project currently configures no dedicated linter, static
type checker, or security scanner, and the package has no runtime dependencies. Do not
claim those checks ran unless the corresponding tool is deliberately added and documented.

On Debian/Ubuntu's patched setuptools, `--no-build-isolation` fails with
`AttributeError: install_layout` before it ever reaches this package. Drop the flag there
and build with isolation instead.
