---
name: scryntic-task-workflow
description: Use when implementing, continuing, or reviewing an explicitly approved Scryntic task, including resuming an interrupted task from a handoff.
---

# Scryntic Task Workflow

Apply this workflow to one approved task. It does not authorize another task, architecture changes, tracker writes or GitHub publication. For review-only requests, inspect and report without implementing changes.

All paths below are relative to the repository root.

1. Read `AGENTS.md` and retain the user's task scope and authorization.
2. Read only the relevant sections of `ARCHITECTURE.md`, including applicable security invariants and completion criteria.
3. When Linear is available, inspect the current issue, blockers and dependencies. Reconcile its scope with the architecture and repository evidence. If Linear is unavailable, use explicitly supplied issue/task context, disclose the limitation and do not invent dependency status. Stop dependent work if required approval or blocker information is missing.
4. Before editing, inspect the current branch/HEAD, `git status`, relevant staged/unstaged diffs and untracked files. Verify prerequisite implementation in the repository; preserve existing changes. Follow the code-discovery rules in `AGENTS.md`.
5. Implement only the approved task with simple, maintainable changes. Avoid unrelated cleanup. Preserve application/security boundaries; never introduce financial execution authority or expose secrets.
6. If implementation requires changing architecture, scope, security boundaries or dependencies, stop affected work, explain the contradiction and request approval. Do not silently substitute a workaround.
7. Run the task's relevant tests/checks and compare observed results with each completion criterion. Record commands, outcomes and checks not run. Never claim completion or mark the issue complete when required validation is missing or failing.
8. Review the final relevant diff, new-file contents and `git status`. For substantial tasks, save a concise task report with the reporting skill, then update `docs/HANDOFF.md` as described below before the final response, including blocked or incomplete work. Report changes, validation evidence and remaining risks. Update Linear only when authorized, reflecting implementation progress without redefining architecture. Commit, push or create a PR only when explicitly requested.
9. When context or usage is getting low, stop starting new work. Leave a recoverable state and produce the handoff below; do not claim unfinished work is complete.

## Handoff

Keep `docs/HANDOFF.md` as the current local checkpoint for this checkout. Update it after substantial tasks and before an unfinished task is handed over; skip trivial questions and routine status updates. Link the detailed task report and retain unresolved main-task context instead of appending a session log. On resume, read and verify the checkpoint. Repository/tracker artifacts remain in English; conversational reporting follows `AGENTS.md`.

Keep the checkpoint short, usually 20–40 lines, with a UTC timestamp and task state. Use “none” when no task work remains; do not invent another task to fill the next-step field. Preserve pre-existing user changes and unresolved main-task context.

Include:

- **Current task:** approved scope and Linear issue ID/link, if available.
- **Completed:** verified outcomes only.
- **Changed:** affected paths and purpose, separating pre-existing changes.
- **Validation:** exact commands, results, failures and checks not run.
- **Current repository state:** branch/HEAD, staged/unstaged/untracked changes and publication state.
- **Incomplete:** unmet completion criteria.
- **Problems/risks:** blockers, uncertainties and architectural conflicts.
- **Remaining work:** pending actions, recorded under Incomplete in the handoff.
- **Single best next step:** one concrete action, recorded under Next step.
- **Resume context:** relevant architecture sections, issue/dependencies, decisions, file locations and necessary non-secret commands.

On resume, recheck repository and issue state; a handoff is context, not proof that validation or dependencies are still current.
