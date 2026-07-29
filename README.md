# ALTAI

ALTAI is a local project-completion operating system for Claude Code and Codex. It scans a
repository, builds a model of what the project is *for*, forms a task graph, prepares
task-specific research instructions, and forces implementation through evidence-based
quality gates.

Requires Python 3.10+. No dependencies.

## Install into a project

```bash
python scripts/install_into_project.py /path/to/your-project
cd /path/to/your-project
python .altai/tool/run.py start .
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
exact start, continue, and design commands. Re-running the installer updates ALTAI-owned
files while preserving existing `AGENTS.md` and `CLAUDE.md` content.

Optionally install the CLI globally instead of vendoring it:

```bash
pip install -e .
altai start /path/to/your-project
```

## Use it

Open the project in Claude Code or Codex and say:

```text
Continue.
```

The host agent reads `CLAUDE.md` / `AGENTS.md`, loads the ALTAI skill, and runs the loop.

## Project intelligence layer

Beyond the task graph, ALTAI keeps a model of the project itself under `.altai/`:

* **`project-model.json`** — the project's declared purpose, audience, core flow and
  non-goals, extracted from README/docs/manifests and confirmed by the host agent.
* **`code-graph.json`** — a file → class → function → call graph (`ast` for Python, a
  regex pass for other languages), used to guess which files a task likely touches.
* **`.altai/memory/`** — five category files (architecture, product-decisions,
  coding-conventions, failed-approaches, user-preferences) plus a structured
  `learned-rules.json`, written explicitly by the host agent via `altai learn` / `altai
  rule` and re-surfaced in every later task's brief.
* **`opportunities.json`** — scored improvement candidates the repository never named by
  ID (an oversized function, a name duplicated across files, a heavily-called function no
  test appears to cover). These are never added to the task graph automatically — a
  candidate *creates* new intent, unlike a gap, which only closes a contradiction in intent
  the repository already declared. `altai promote <id>` is the one, deliberate path from
  candidate to real task.

A **gap analyzer** compares what `project-model.json` declares against what the repository
actually has — an unconfirmed purpose, a declared test command with no tests, tests with no
command to run them, an entry point with no run command — and opens a `gap-*` task for each
contradiction that closes itself once resolved. Confirming the project's purpose is
exempt from (and gates) `quality-gates`: nothing else proceeds until the project's own
intent is confirmed, not merely derived from documentation.

## Product design and UX architecture

Use the opt-in design pass after the host agent has confirmed `project-model.json`:

```bash
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

`--design` does not write application UI, browse the web, launch the project, take
screenshots, or claim visual conformance. Claude Code or Codex performs benchmark research
and implementation. Afterwards, `VisualVerifier` can validate the host agent's recorded
build, screenshot, mobile-width, console, and primary-flow evidence.

`altai autopilot` runs one bounded rescan and reports the single next actionable task
(with related files and memory attached), the top open opportunities, and a policy check
on the active task's own text against the stop-and-ask categories below. It does not
implement, research or test anything itself — that stays with the host agent under the
normal loop.

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

`next` exits 0 when it hands out a task, 3 when the project is blocked, and 4 when it is
complete. `autopilot` adds exit code 5: the returned task's own text matched a
stop-and-ask category (see Safety) and should not be implemented without the user's
agreement. A shell loop can branch on any of these without parsing status text.

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
├── AGENT_TASK.md         # generated loop instructions for the host agent
├── research/             # one note per task, written by the agent
├── evidence/             # append-only evidence per task
├── runs/log.md           # audit trail of every state change
└── tool/                 # vendored ALTAI package + run.py launcher
```

## Important limit

“No API key” means ALTAI itself never calls a model API. Claude Code or Codex still needs
its normal signed-in account or configured model access. Web research is performed by the
host agent, not by this package — ALTAI only tells it what to look for and where to prefer
looking. The same applies to `opportunities.json`: every score is derived mechanically from
the code graph and project model, never from competitor research or a guessed "market
value" — ALTAI does not fetch anything on its own.

## Safety

ALTAI does not silently publish, deploy, delete data, purchase services, expose secrets,
or make unsupported product decisions. `altai autopilot` keyword-checks a task's own text
against five categories — destructive, credentials, spending, publish, irreversible
product decision — and flags rather than proceeds when one matches (exit code 5). This is
a hint from the task's own words, not enforcement: ALTAI has no sandbox of its own. Real
enforcement of what the host agent can do belongs to Claude Code / Codex configuration
(`.claude/settings.json` permissions and hooks), not to this package.

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
