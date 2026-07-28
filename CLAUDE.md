# ALTAI Operating Contract

Use `.claude/skills/altai/SKILL.md` automatically whenever the intent is to continue,
finish, repair, or complete this project.

Do not wait for detailed prompts. Infer the goal from README, tests, issues, TODOs, git
history, and current failures. Work through the dependency graph until verified
completion.

Get work with `python .altai/tool/run.py next` (or `altai next`). Record every outcome
with `done` / `fail` / `block` — never edit `.altai/project-state.json` by hand, or the
recorded progress and evidence trail are lost.

Use web research only when the active task needs it, and save compressed evidence under
`.altai/research/`.

Human approval remains mandatory for destructive actions, credentials, spending,
deployment/publication, and ambiguous product decisions.
