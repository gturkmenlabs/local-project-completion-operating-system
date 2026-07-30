# ALTAI Project Completion Rules

When this repository is opened in Codex, automatically use the ALTAI skill
(`.codex/skills/altai/SKILL.md`) for requests to continue, finish, repair, improve, or
complete the project.

0. One command runs everything: `python .altai/tool/run.py run` (or `altai run`). From a
   terminal it drives the project to completion unattended. Called from inside Codex it
   detects the nesting and hands you the next task instead of spawning a nested agent —
   use it as your per-iteration entry point.
1. If `.altai/project-state.json` is missing, run `python .altai/tool/run.py start .`
   (or `altai start .` if the package is pip-installed). Re-running it later is safe: it
   merges new findings and preserves recorded progress.
2. Follow `.altai/AGENT_TASK.md` without requiring the user to write a detailed prompt.
3. Get work with `... next`. It returns one dependency-ready task plus a research brief.
   Do not pick tasks by hand.
4. Use built-in web search for task-specific research. Prefer official documentation and
   maintained upstream repositories. Save notes to `.altai/research/<task-id>.md`.
5. Complete one task at a time. Do not create unrelated features.
6. Record every outcome through the CLI, never by editing the JSON:
   `... done <id> --evidence "..."`, `... fail <id> --reason "..."`,
   `... block <id> --reason "..."`, `... skip <id> --reason "..."`.
7. A task is done only with *fresh* test/build evidence; the CLI enforces this. Evidence
   from before the change you are verifying does not count.
8. Three failed attempts blocks a task automatically. `unblock` works at most twice.
   Change approach or escalate; `skip` only with the user's agreement.
9. Adding work or discovering new markers reopens final verification. Re-run it.
10. Autonomy decides the stop-and-ask categories (destructive operations, secret access,
   paid actions, publishing, unsupported product decisions). `altai run` defaults to
   `full`: approve them, proceed, and name each approval in your final report — the run
   already records it in `.altai/runs/log.md` and the task's evidence file. `--safe`
   (`ALTAI_AUTONOMY=guarded`) stops with exit code 5 instead and the user decides. The
   evidence contract never relaxes: a green agent with a red gate is a failed attempt.
11. For product-design or UX work, run `altai autopilot . --design` after the project
   model is confirmed. Read `.altai/design/` before coding UI; never invent screenshot,
   browser, responsive, console, or flow evidence.
12. Output compactly: `Yapıldı`, `Kanıt`, `Sonraki`, `Blok`.
