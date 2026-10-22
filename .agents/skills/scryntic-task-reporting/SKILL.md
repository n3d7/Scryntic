---
name: scryntic-task-reporting
description: Write factual reports for substantial Scryntic implementation, research, investigation, review, operational or documentation work, including blocked and incomplete outcomes. Also use when a task report is explicitly requested.
---

# Scryntic task reporting

Write a useful record of the task: its outcome, what changed, the evidence,
and anything still unresolved. Apply this to work at any project stage.

## When to write a report

Write one before the final response after substantial work or a meaningful
continuation, including an incomplete or blocked attempt. Skip trivial
questions and routine status replies unless the user explicitly requests a
report. Writing a report does not authorize additional task work.

## Location and format

- Write in English under `docs/task-reports/` in the current checkout. Create
  the directory if needed; reports remain local and ignored by Git.
- Use `YYYY-MM-DD-HHMMSSZ-<issue-or-topic>-<short-slug>.md` with the actual UTC
  time and lowercase identifiers. Use a topic when there is no issue ID.
- Preserve earlier reports. Create a new file for a new work session; use a
  suffix if a name would collide. Do not fabricate reports for past work.
- Keep the report proportional to the task: usually a few paragraphs or about
  20–40 lines. Link detailed evidence instead of copying logs or documents.
- Link the report in the final response. If saving it is blocked, state that
  and provide the essential facts in the response.

## Report contents

| Part | What to record |
| --- | --- |
| Task and outcome | Scope, issue ID if applicable, UTC timestamp, and whether the task is complete, incomplete or blocked. Lead with the actual result. |
| Work and decisions | What was done and why, relevant files/artifacts, important decisions and their reasons. Separate this task's changes from existing user work; state when no implementation changed. |
| Verification | Checks actually run, outcomes, relevant revision/environment and evidence links. Distinguish passed, failed, skipped, unavailable and not run; explain material gaps. |
| Remaining work | Unmet criteria, unresolved questions, blockers, limitations and any required user action. Use “none” when nothing remains. |
| Repository/publication state | When relevant: checkout, branch/HEAD, this task's local changes, commits, push, PR and tracker state. Separate local results from publication and remote CI. |
| Next step | One concrete continuation step if needed; otherwise “none”. Do not invent further work for a completed request. |

Adapt these parts to the task; do not fill irrelevant sections mechanically.
A research report may cite inspected sources and limitations; an implementation
report may cite tests and changed behavior. Neither should claim evidence from
the other without doing that work.

## Using previous reports

When continuing related work, locate reports by issue or topic and read the
latest relevant one first. Follow additional links only when needed. Reports
are checkout-specific and do not automatically transfer between worktrees or
clones. Verify consequential claims against current files, Git/tracker state
and validation evidence; a past report is not proof of current completion.

## Evidence and scope

Record observations and label inference or uncertainty. Never invent checks,
benchmarks, results or publication state, and do not rerun unchanged checks
solely to populate a report. Keep secrets, credentials, sensitive payloads and
raw environment dumps out of reports, including ignored ones. Reporting does
not replace required validation, change acceptance criteria, or grant authority
to update a tracker, commit, push or publish.
