# Changelog

## 0.4.0

Opportunity discovery and a bounded control-layer wrapper, on top of the intelligence
layer from 0.3.0.

* **Opportunity finder** (`altai/intelligence/opportunity_finder.py`), `.altai/opportunities.json`.
  Scores candidates the repository never named: a function over ~60 lines, a name declared
  in 3+ files (possible duplication), a function called from 3+ places with no test file
  covering it. Every input is mechanically derived from `.altai/code-graph.json` and
  `project-model.json` — no web search, no competitor analysis, no guessed "user value".
  Unlike a gap, an opportunity *creates* new intent rather than closing a declared
  contradiction, which is exactly what `CLAUDE.md`'s contract calls an ambiguous product
  decision — so candidates are never auto-injected into the task graph. `altai
  opportunities` lists them; `altai promote <id>` is the one, deliberate path from
  candidate to real task.
* **Policy engine** (`altai/policy_engine.py`). Keyword-classifies a task's own text against
  `CLAUDE.md`'s existing stop-and-ask categories (destructive, credentials, spending,
  publish, irreversible product decision). It enforces nothing — ALTAI has zero
  dependencies and no sandbox of its own, so it cannot block a network call or file write
  the way `.claude/settings.json` permissions and hooks can. It only flags, from a task's
  own words, that a human should look before the host agent proceeds.
* **`altai autopilot`** (`altai/autopilot.py`). One bounded rescan that reports the single
  next actionable task (with `related_files` and `memory`, same as `next`), the top open
  opportunities, and a policy check on the active task. It does not implement, research or
  test anything itself — that stays with the host agent under `SKILL.md`'s existing loop.
  Exit code `5` means the active task's own text tripped a policy category.
* `SKILL.md` (Claude and Codex copies) documents both.

## 0.3.0

Project intelligence layer: ALTAI now tracks *why* the project exists and *where* things
live, not only what markers are unresolved.

* **Gap analyzer** (`altai/intelligence/gap_analyzer.py`). `project-model.json` was built
  and saved on every `start` but nothing ever read it back. It now drives the task graph:
  an unconfirmed purpose/audience/flow, a declared test command with no tests, tests with
  no command to run them, or an entry point with no run command each open a `gap-*` task
  that closes itself once the underlying condition clears, the same way a resolved TODO
  drops out of a rescan. `final-verification` now waits on these like any other task.
  `gap-confirm-project-model` is exempt from the forced `quality-gates` dependency every
  other task gets — confirming what the project is *for* has to come before quality gates
  are established, not after (`planner.PURPOSE_FIRST_IDS`).
* **Code graph** (`altai/intelligence/code_graph.py`). Persisted to `.altai/code-graph.json`
  on every `start`/`status --rescan`. Python is parsed with `ast` for real classes,
  functions, methods and (unresolved, best-effort) call names; every other supported
  language gets a regex pass over top-level declarations only. `altai next` now attaches a
  `related_files` guess — the task's title and description matched against symbol names and
  file paths — so the host agent has a starting point instead of grepping the whole tree.
* **Project memory** (`altai/intelligence/project_memory.py`), `.altai/memory/`. Five
  category files (architecture, product-decisions, coding-conventions, failed-approaches,
  user-preferences) plus a structured `learned-rules.json`. Nothing is written to it
  automatically — the host agent records a decision explicitly with the new `altai learn
  <category> "<note>"` / `altai rule "<condition>" "<rule>"` commands. A digest is folded
  into `next`'s brief and into `AGENT_TASK.md` once anything has been recorded.
* **Two new subagent roles**: `altai-analyst` (confirms the interpretive fields of
  `project-model.json` against the repository) and `altai-architect` (plans a multi-file
  task using the code graph before any code is written). `SKILL.md` (Claude and Codex
  copies, kept identical) now points to both.

## 0.2.3

Found by installing ALTAI into a real Next.js monorepo.

* **Stack detection missed subdirectory projects.** A repo whose app lives in `web/`
  reported `Stack: belirsiz`. Markers are now probed in the root and up to two levels of
  non-ignored subdirectories.
* **Documentation was mined for fake tasks.** Every occurrence of the word "TODO" counted,
  so skill reference docs produced tasks titled `';` and `) 555-0100...` while the actual
  source tree had none. A marker now only counts when it is a real annotation: behind a
  comment starter in code, or at the start of a line (optionally behind a bullet or
  heading) in markdown. Titles are also stripped of stray quoting.
* **Ignore list extended** with `.agents`, `.autoresearch` and `.cursor`.
* **A leaked lock file stalled every later command for 15 minutes.** If the PID recorded
  in the lock belongs to a process that no longer exists, the lock is now broken after a
  two-second grace period instead of waiting for the stale timeout. Unparseable or
  unreadable ownership is still treated as live.

Tests: 86 → 103.

## 0.2.2

Fixes for defects found in a second adversarial review of 0.2.1.

* **Stale evidence satisfied `done`.** Completion accepted evidence recorded before the
  change being verified, so a reopened task could be re-closed with `-e ""`. Every
  completion now requires fresh, non-blank evidence, and reopening clears the evidence
  that described the superseded state.
* **`fail` was a back door around the `done` guards.** It set `CODING` unconditionally,
  which erased a manual block and reverted a completed task. It now refuses blocked and
  settled tasks.
* **A rescan that discovered new work left `final-verification` closed.** Reopening is now
  handled centrally by `planner.reconcile_final`, so it fires for `add`, `start`, `fail`
  and any other path that leaves unfinished work.
* **A refused `unblock` still consumed the unblock budget** because state was persisted
  before the check. The graph is now dry-run first; a refusal records nothing.
* **Tracebacks escaped the CLI.** `main` caught only `FileNotFoundError`; a `PermissionError`
  or a hand-mangled state file produced a traceback instead of a `Hata:` line. It now
  catches the whole `OSError` family, argparse's `SystemExit` returns an exit code, and
  the state loader validates field types.
* **Non-ASCII titles could not be added.** `str.isalnum()` is true for `ö`/`ş`/`ı`, so
  `add "Görev tamamlansın"` produced an ID the validator then rejected — a first-contact
  failure in a Turkish-language tool. Slugs are now transliterated to ASCII.
* **Migration duplicated completed legacy work.** A DONE `todo-N` survived alongside the
  content-addressed task generated for the same marker. All positional IDs are now
  dropped, with a recorded risk naming what was lost. Migration also no longer runs as a
  side effect of an unrelated mutation: mutating a legacy project asks for `start` first.
* **Stale-lock recovery could cascade.** A departing process deleted whatever lock was
  present, including one a third process had just taken. Locks now carry an owner token
  and are only removed by their owner; the stale threshold moved from 60s to 15min and
  the repo scan happens outside the critical section.
* **`.altai/.gitignore` was only written for a brand-new workspace**, so an upgraded
  project never ignored the vendored `tool/`. Missing entries are now appended.
* **`apply_blocks` edge cases**: a settled task can no longer be auto-blocked; clearing an
  auto block restores the prior status instead of flattening to `UNKNOWN`; the reason now
  prefers the broken edge over the exhausted budget; duplicates removed.
* **Added `skip <id> --reason`.** One permanently blocked task used to make completion
  unreachable, leaving fabricated evidence as the only escape. `SKIPPED` is a settled
  status that satisfies dependencies and is reported separately in the status line.
* **Risks were invisible outside `--json`**, so a truncated 50-marker scan looked clean.
  They now print in `status`, and scan-derived risks clear when the condition does.
* `next --json` emits JSON on the blocked and complete paths too, not just when it hands
  out a task.
* A typo'd `--path` no longer silently creates a workspace in a non-existent directory.
* The state file is written 0644 (minus umask) rather than 0600, so a second account can
  read it.

Tests: 57 → 86.

## 0.2.1

Fixes for defects found in an adversarial review of 0.2.0.

* **Deadlock via `add --depends-on final-verification`.** `final-verification` depends on
  every other task, so any edge into it was a guaranteed cycle that no CLI command could
  clear — the project could never reach `BITTI`. Such edges are now dropped by
  `enrich_plan` with a note on the task.
* **v0.1 state files duplicated every task.** `schema_version` was stored but never acted
  on, and tasks predating the `discovered` flag loaded as hand-added and therefore
  immortal. Added `memory.migrate()`; unfinished legacy `todo-N` entries are dropped so
  the fresh scan can re-create them with stable IDs.
* **`done -e ""` satisfied the evidence requirement.** Evidence is now stripped and blank
  entries rejected.
* **Concurrent commands lost updates.** Every mutation is a load-modify-write with no
  locking, so two agents working in parallel clobbered each other. Added a lock file with
  timeout and stale-lock recovery around every mutation.
* **`unblock` allowed infinite retry loops** by resetting `attempts` to 0 with no ceiling.
  Unblocks are now counted and capped at 2, after which the CLI demands escalation.
* **Stale auto-block reasons.** `apply_blocks` skipped anything already blocked, so a
  "dependency does not exist" reason survived after the dependency was created. Auto
  blocks are now cleared and recomputed each run; manual blocks stay sticky.
* **`add` after completion left final verification stale.** Adding work now reopens a
  `DONE` `final-verification` instead of silently breaking the "final gates everything"
  invariant.
* **`done` worked on blocked tasks**, erasing a human gate. Now refused.
* **Task IDs were unvalidated** and interpolated into evidence paths, so
  `--id ../../..` wrote outside the project. IDs are now validated.
* **`find_cycles` was recursive** and raised `RecursionError` on long chains. Rewritten
  iteratively; verified on a 3000-task chain.
* **`.altai/tool/` was not gitignored** and `.DS_Store` was vendored. Both fixed.
* **The 50-marker scan cap truncated silently.** It is now recorded as a project risk.
* **`next` returned 0 whether or not it produced a task.** Exit codes are now 0 = task,
  3 = blocked, 4 = complete.

Tests: 39 → 57.

## 0.2.0

Every issue found in the v0.1 review is fixed. The v0.1 package could not be installed and
its documented entry command did nothing.

### Fatal

* **`python -m altai.cli` was a silent no-op.** `cli.py` had no `if __name__ == "__main__"`
  guard, so the command documented in the README, `AGENTS.md` and both `SKILL.md` files
  exited 0 without creating anything. Added the guard and a `main(argv)` signature that
  returns a real exit code.
* **`pip install -e .` failed.** Setuptools flat-layout auto-discovery aborted with
  *"Multiple top-level packages discovered"*. Added an explicit
  `[tool.setuptools] packages = ["altai"]`.
* **`.altai/AGENT_TASK.md` was generated from a broken string literal**, so the file the
  agent reads was full of stray quotes and indentation. Replaced with a proper template.

### State and control flow

* **Rescans no longer destroy progress.** `bootstrap()` used to overwrite
  `project-state.json` with a fresh scan on every invocation, wiping status, attempts and
  evidence. Added `load_state()` and `merge_state()`; writes are now atomic via
  `os.replace`.
* **Added the mutation commands the loop was missing**: `next`, `done`, `fail`, `block`,
  `unblock`, `add`. Previously nothing could mark a task complete, so `TaskStatus.DONE`
  was unreachable and `project_complete()` could never return `True`.
* **`done` enforces the contract**: it refuses a task with no evidence and a task whose
  dependencies are unfinished.
* `status` no longer rescans by default (`--rescan` opts in), and gained `--json`.

### Task graph

* **Content-addressed task IDs.** Positional `todo-1` / `todo-2` IDs remapped to different
  work whenever a marker was resolved. IDs are now `todo-<sha1(path+text)>`.
* **No more silent deadlocks.** Exhausted attempt budgets, dependency cycles and missing
  dependencies previously removed a task from the candidate list without marking it,
  leaving the agent with no next task and no completion. All three now produce an explicit
  `BLOCKED` status with a recorded reason, and `project_phase()` reports `BLOCKED`.
* `enrich_plan()` is idempotent — it no longer grows dependency lists on each run.

### Scanner

* Prunes ignored directories during the walk instead of filtering afterwards; ignore list
  extended (`node_modules`, `target`, `.next`, `__pycache__`, `.claude`, `.codex`, …).
* Skips symlinks and files over 512 KB; deduplicates identical markers.

### Installer

* Stops copying the `altai/` package into the project root. It is vendored under
  `.altai/tool/` with a `run.py` launcher, so it works from the project root and collides
  with nothing.
* **An existing `CLAUDE.md` / `AGENTS.md` is preserved.** The ALTAI block is spliced in
  between markers and updated in place on reinstall.

### Research

* Dropped the `google.com/search` URL — search-engine result pages are not fetchable by
  agent tooling. The brief now emits plain query strings for the host's own web search,
  plus a ranked list of preferred official-documentation domains per detected stack.

### Compatibility

* `requires-python` lowered from 3.11 to 3.10 (`enum.StrEnum` replaced with `(str, Enum)`).
* Tests: 3 → 39.
