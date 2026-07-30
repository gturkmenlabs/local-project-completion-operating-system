# ALTAI Operating Contract

Use `.claude/skills/altai/SKILL.md` automatically whenever the intent is to continue,
finish, repair, or complete this project.

Do not wait for detailed prompts. Infer the goal from README, tests, issues, TODOs, git
history, and current failures. Work through the dependency graph until verified
completion.

One command runs everything: `python .altai/tool/run.py run` (or `altai run`). From a
terminal it drives the whole project to completion unattended. Called from inside this
session it detects the nesting and hands you the next task instead of spawning a nested
agent — use it as your per-iteration entry point, then record the outcome and call it
again.

Get work with `run` (or `next`). Record every outcome with `done` / `fail` / `block` —
never edit `.altai/project-state.json` by hand, or the recorded progress and evidence
trail are lost.

Use web research only when the active task needs it, and save compressed evidence under
`.altai/research/`.

For product-design or UX work, run `altai autopilot . --design` after the project model is
confirmed. Read `.altai/design/` before coding UI and never fabricate rendered evidence.

Autonomy is a setting, not a habit. `altai run` defaults to `full`: destructive actions,
credentials, spending, deployment/publication and ambiguous product decisions are approved
automatically, and every approval is recorded in `.altai/runs/log.md` and the task's
evidence file. Proceed under that standing approval and name what you approved in your
final report. `altai run --safe` (or `ALTAI_AUTONOMY=guarded`) restores the hold: the run
stops with exit code 5 and the user decides.

Autonomy never relaxes the evidence contract. A task completes only when the project's own
checks pass; a green agent with a red gate is a failed attempt.
