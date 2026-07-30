# Benchmark: autonomous coding-agent harnesses, July 2026

Research pass behind the `altai run` work. The question was narrow: among products that
already do what ALTAI is trying to do — take a repository from "unfinished" to "done" with
minimal human prompting — which capabilities are standard, and which of them did ALTAI not
have?

Sources are listed at the bottom with the date they were read. Everything below is either
implemented in this repository or recorded as a deliberate non-goal.

## What comparable products do

| Capability | Where it is standard | ALTAI before | Now |
| --- | --- | --- | --- |
| Orchestrator loop: agent acts, harness validates, failure feeds back as the next prompt | TaskRunner-style orchestrators, ralphy, loop-harness | absent — a human ran the loop | `altai run` |
| Headless agent invocation as the execution primitive | Claude Code `-p`, `codex exec` | absent | `altai/executor.py` |
| Harness-side validation instead of agent self-report | loop-harness verification gate, TaskRunner test/lint step | evidence existed but a human produced it | runner runs the project's own gates |
| Commit-per-completed-step | near-universal; "git adds versioning to the filesystem so agents can rollback errors" | absent | `altai/checkpoint.py` |
| Rollback to the last good point on failure | agent rollback/checkpoint patterns | absent | reset + clean to the previous checkpoint |
| Cost and turn guards for unattended runs (`--max-turns`, timeouts, budget caps) | Claude Code CI/CD guidance | timeouts only | `--max-turns`, `--time-budget`, per-task timeout, cost totals |
| Structured run output (`--output-format json`: cost, turns, session id) | Claude Code headless | absent | parsed into the run report |
| Spec/plan artifacts before implementation | BMAD-METHOD, Spec Kit, Agent OS, OpenSpec | project model + design pass existed but were opt-in | design pass attempted by default |
| Persistent project memory across tasks | BMAD file-based context passing, Task Master | existed (`.altai/memory/`) | now also written automatically when a recommendation is adopted |
| Worktree isolation, parallel agents | loop-harness, swarm-protocol, opengoat | absent | **not adopted** — see below |
| Second-agent verification gate | loop-harness | subagent definitions only | **not adopted** — see below |

## What was adopted, and why

1. **Close the loop** (`altai run`). Every comparable product's core is orchestrator →
   agent → validate → feed failure back. ALTAI had every part except the orchestrator, and
   the missing part was the one a human was substituting for.
2. **Commit per task, roll back on failure.** The dominant safety primitive, and the one
   that matters most once approvals are disabled: it is what makes an unattended run
   reversible without a private snapshot format. Refused on a dirty tree, because a commit
   would bury uncommitted human work and a reset would delete it.
3. **Cost and turn ceilings.** The consistent warning in unattended-automation guidance is
   that a retry loop is what burns a budget, not a single expensive task. ALTAI's
   three-attempt cap already bounded retries; per-task turn and time ceilings bound the
   attempt itself, and the run reports what it spent.
4. **Structured agent output.** `--output-format json` is the only way a headless run can
   report cost, turns and session id, so it is now the default for the `claude` agent.
   Nothing is assumed for other CLIs: an invented flag makes a CLI refuse to start, which
   is worse than a missing cost line.
5. **Design/spec before code, by default.** Spec-driven frameworks put an inspectable
   artifact between intent and implementation. ALTAI already generated one; it was opt-in,
   which meant most runs skipped it. It is now attempted every run and skipped with a note
   when the project model is unconfirmed.
6. **Adopted recommendations reach memory.** BMAD's file-based context passing exists so a
   later agent knows why earlier work happened. ALTAI promoted opportunities into tasks but
   recorded nothing about the decision; each promotion now writes a `product-decisions`
   entry in the same pass that creates the task.

## What was deliberately not adopted

* **Worktree isolation and parallel agents.** Worktrees are the standard isolation
  primitive, and parallelism is where the harnesses above spend most of their complexity
  (conflict detection, heartbeats, handoff protocols). ALTAI's task graph hands out exactly
  one dependency-ready task at a time by design, so isolation would buy nothing today.
  Revisit only alongside actual parallel execution.
* **Multi-persona SDLC role-play** (BMAD's Analyst/PM/Architect/QA personas). ALTAI's
  equivalent — the analyst, architect, researcher and verifier subagent definitions — is
  already thinner on purpose, and the reported cost of the full persona treatment is the
  main criticism of that approach.
* **A second-agent review gate before accepting work.** Genuinely valuable, and roughly
  doubles per-task cost. The project's own declared gates are the cheaper 80% and are now
  enforced by the runner rather than by the agent; a review gate is the next candidate, not
  a silent default.

## Sources

Read 2026-07-30:

* [Claude Code as an Autonomous Agent: Advanced Workflows (2026)](https://www.sitepoint.com/claude-code-as-an-autonomous-agent-advanced-workflows-2026/) — orchestrator/TaskRunner loop, validation feedback.
* [Claude Code in CI/CD and Headless Automation](https://hidekazu-konishi.com/entry/claude_code_cicd_and_headless_automation.html) — `-p`, output formats, `--max-turns`, timeout and budget guards.
* [Agent Rollback and Checkpoint Patterns: A Reference](https://www.digitalapplied.com/blog/agent-rollback-checkpoint-patterns-2026-engineering-reference) — commit-per-step discipline, mutation tiers.
* [loop-harness](https://github.com/lSAAGl/loop-harness) — worktree isolation, second-agent verification gate, staged outputs.
* [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators) — ralphy, swarm-protocol, opengoat.
* [BMAD vs Spec Kit vs OpenSpec (2026)](https://medium.com/@reenbit/bmad-vs-spec-kit-vs-openspec-choosing-your-spec-driven-ai-framework-in-2026-a6996b3ebb8d) and [Spec-kit, BMAD, Agent OS and Kiro](https://medium.com/@tim_wang/spec-kit-bmad-and-agent-os-e8536f6bf8a4) — spec-driven phases, persona costs, file-based context passing.
