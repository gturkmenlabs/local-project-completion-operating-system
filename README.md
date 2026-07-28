# ALTAI

ALTAI is a local project-completion operating system for Claude Code and Codex. It scans a
repository, forms a task graph, prepares task-specific research instructions, and forces
implementation through evidence-based quality gates.

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

Optionally install the CLI globally instead of vendoring it:

```bash
pip install -e .
altai start /path/to/your-project
```

## Use it

Open the project in Claude Code or Codex and say:

```text
Devam et.
```

The host agent reads `CLAUDE.md` / `AGENTS.md`, loads the ALTAI skill, and runs the loop.

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
  work — `add`, a rescan that finds new markers, a reverted task — reopens it and discards
  the evidence that described the superseded state.
* **Retries are bounded.** Three failed attempts blocks a task; a task may be unblocked at
  most twice before the CLI insists on human escalation. Use `skip` to settle work that
  genuinely will not be done, so one hard block cannot make completion unreachable.
* **Parallel-safe.** Mutations take a lock, so two agents cannot clobber each other's
  recorded work.

`next` exits 0 when it hands out a task, 3 when the project is blocked, and 4 when it is
complete, so a shell loop can branch without parsing the status text.

## State layout

```text
.altai/
├── project-state.json   # task graph + progress (managed by the CLI, do not hand-edit)
├── AGENT_TASK.md        # generated loop instructions for the host agent
├── research/            # one note per task, written by the agent
├── evidence/            # append-only evidence per task
├── runs/log.md          # audit trail of every state change
└── tool/                # vendored ALTAI package + run.py launcher
```

## Important limit

“No API key” means ALTAI itself never calls a model API. Claude Code or Codex still needs
its normal signed-in account or configured model access. Web research is performed by the
host agent, not by this package — ALTAI only tells it what to look for and where to prefer
looking.

## Safety

ALTAI does not silently publish, deploy, delete data, purchase services, expose secrets,
or make unsupported product decisions.

## Development

```bash
python -m pytest -q
```
