# Scryntic Workflow

`ARCHITECTURE.md → Linear issue → implementation → validation → GitHub`

1. **Architecture:** [ARCHITECTURE.md](../ARCHITECTURE.md) defines the approved design, Foundation v1 scope and security invariants. Its roadmap does not automatically authorize implementation.
2. **Linear issue:** Select one approved task with explicit scope, dependencies and completion criteria. Linear owns implementation planning and status; verify blockers before dependent work.
3. **Implementation:** Follow local `AGENTS.md` and the [task skill](../.agents/skills/scryntic-task-workflow/SKILL.md). Inspect actual repository state, preserve existing changes and change only what the task requires.
4. **Validation:** Run relevant checks against completion criteria, inspect the final diff and `git status`, and report evidence. Missing or failing validation keeps the task incomplete.
5. **GitHub:** The repository records actual implementation; GitHub records published commits and review history. Commit, push or create a PR only when explicitly requested. Validated local work is not automatically published or merged.

Linear must reflect actual implementation progress, validation and blockers. Update it when authorized; if access or authorization is unavailable, report the pending update. Never silently redefine architecture or dependencies through an issue edit. If the task conflicts with the architecture, stop affected work and request approval before proceeding.

After each substantial approved task or continuation, including blocked or incomplete work, write a concise local task report using the [reporting skill](../.agents/skills/scryntic-task-reporting/SKILL.md). Separately, refresh local `HANDOFF.md` under the [task workflow](../.agents/skills/scryntic-task-workflow/SKILL.md) before the final response. The handoff is the current checkpoint for this checkout, not an empty template or an append-only log. Keep detailed history in the reports and link the latest relevant report from the handoff. Trivial questions, routine status updates and brief side requests do not replace the main task's context.

For interrupted sessions or low context/usage, stop starting new work and preserve verified progress, remaining work, repository state and one best next step in the handoff. On resume, read it and verify current state before relying on its claims. Filled handoffs and task reports remain local and are included in the configured document backups; updating them grants no authority to begin another task or publish changes.
