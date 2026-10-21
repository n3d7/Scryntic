# Implementation reconciliation

Snapshot: 2026-10-18. This is a bounded architecture inventory, not a fresh full security or release qualification. Historical test results below were read, not rerun in this documentation task.

## Ground truth

Local `main` and GitHub `main` both resolve to `8e1465fd389e440763ef69b7c59832ad2f8bd8de`, the merge of PR #30, F24 CLI/application services and optional TUI. The connected GitHub commit API supplied the remote SHA; no fetch, merge or push was necessary.

At initial redesign reconciliation, the connected Linear project read and complete 51-issue listing (no next page) showed all top-level F01–F24 Done, including sub-issues, with F25–F30 Backlog. Foundation was In Progress, Operational Foundation 29% and release qualification 0%. That initial snapshot was inspected without status changes.

**Subsequent approved disposition:** retire the competing old program while leaving Done untouched. Foundation and its six unfinished F25–F30 issues are now **Canceled as superseded**. All 45 Done issues were compared before/after across returned fields (including description, status, labels and updated/completed timestamps): zero changes. Original project/issue descriptions and blocking relationships remain; applicable acceptance was explicitly carried into PI17/KER-77, PI18/KER-78 and PI21/KER-81. Telegram is optional/deferred, not another active backlog. No Foundation release completion is claimed. [Exact mapping](../architecture/ROADMAP.md#foundation-disposition-after-operator-follow-up).

Detailed relation readback for F25–F30 confirms the existing chain F23 → F25 → F26 → F27, with F21 also blocking F27; F24/F27 → F28/F29 → F30. Preserve those dependencies. The new program links reused responsibilities rather than importing the Telegram dependency into its first forecasting milestone.

| Capability | Current evidence | Interpretation / target gap |
|---|---|---|
| Durable collection, normalization, archive/recovery | F05–F15 Done; [ingestion](../INGESTION.md), [recovery](../RECOVERY.md), [F10 measurements](../performance/f10-operating-envelope.md) | Preserve durable acceptance, explicit coverage uncertainty, immutable no-replace publication and manifest history. Not a promise that every external feed is recoverable. |
| Hostile imports and authenticated pull | F16/F17 Done; [imports](../IMPORTS.md), [pull](../sync/F17_PULL.md) | Preserve restricted parsing, hash/length checks, pinned endpoint identity, trust anchors and read-only SFTP authority. |
| Reproducible datasets | F18 Done; [DATASETS.md](../DATASETS.md); `DatasetService.build` in `src/scryntic/dataset/service.py:94` | Builds against exact accepted manifests; distinct historical reconstruction and as-observed policies; restricted worker response binds exact request; output is installed after verification. Extend to bitemporal families and larger bounded batch scans rather than replace artifacts. |
| Replay / baseline | F19 Done; [REPLAY.md](../REPLAY.md); `ReplayService.prepare` in `src/scryntic/replay/service.py:46` | Explicit train/validation/test cutoffs, chronological feature/label separation, overlapping-label purging, training-only transforms and per-series naive price baseline. Target needs returns, directional probabilities, calibration, repeated walk-forward evaluation and selection-bias accounting. |
| Providers / durable jobs | F20 Done; [JOBS.md](../JOBS.md); `JobService` in `src/scryntic/jobs/service.py:18` | Explicit admission, immutable context and bounded result acceptance exist. Extend with train/experiment/qualification contracts and remote lease fencing; do not create another independent job state machine. |
| Isolated local model | F21/F22 Done; [MODEL_WORKERS.md](../MODEL_WORKERS.md), [F22 validation](../F22_VALIDATION.md) | Qualified CPU launch path and TimesFM integration exist. Operational correctness and repeatability do not establish independent holdout advantage. GPU and arbitrary remote hosts are not thereby qualified. |
| Daemon | F23 Done; [DAEMON.md](../DAEMON.md), [validation](../F23_VALIDATION.md) | Lifecycle, bounded intake, health and actual-owner shutdown exist; deployment/release acceptance remains separate. |
| CLI / TUI | F24 Done; [usage](../F24_USAGE.md), [validation](../F24_VALIDATION.md), `application.operations.Services` | 23 typed workflows, versioned bounded JSON, shared validation, optional Textual 8.2.8, cancellation and installed-service paths. Preserve these contracts and extend the workflow registry. |
| Operations not delivered under the original plan | F25 / KER-29, F26 / KER-30, F27 / KER-31, F28 / KER-32, F29 / KER-33, F30 / KER-34 | Now Canceled as superseded under explicit follow-up. Original scopes remain searchable; mandatory applicable gates move to PI17/PI18/PI21, with PI06 family-policy linkage. Telegram is optional/deferred. Cancellation does not mean delivery. |

## Material discrepancies and disposition

1. The old local `ARCHITECTURE.md` describes a four-file September 14 repository and labels all implementation future work. It is also explicitly ignored, not tracked. Preserve its exact bytes in `docs/architecture/history/FOUNDATION_V1_2026-09-14.md`, remove the ignore rule and replace the canonical document with the proposed target architecture. Historical approval language does not approve the new design.
2. `docs/validation/F24_VALIDATION.md` still says publication is pending and no PR/merge occurred. Current GitHub merge #30 and Linear KER-28 Done supersede that publication snapshot. Retain the historical report and record this reconciliation instead of rewriting its evidence or changing Done status.
3. F22 validation retains failed/blocked earlier checkpoints and later successful checkpoints. Read chronology and revision scope together. Do not treat an old failed attempt as current absence, or successful isolation as forecasting skill.
4. `docs/plans/F24_PLAN.md` has unchecked qualification/publication checklist entries despite subsequent validation and merge. Treat it as a historical execution checkpoint; the new roadmap uses source, validation and current tracker state together.
5. `README.md` still says “Work in progress”; `docs/readme.md` emphasizes older F22 links. Update entry points to the target proposal and current evidence without claiming redesigned capabilities exist.
6. Existing F19/TimesFM evaluations concern bounded candle/price experiments. They do not prove calibrated return predictions, economic usefulness, cross-market generalization, foundation-model pretraining independence or genuine forward performance.

## Source-level inspection and scope

Tier 2 graph project: `local Scryntic codebase index`, initial recorded generation `2026-10-18T16:28:27Z`, later coverage generation `2026-10-18T16:59:29Z`. Symbols discovered through `search_graph`, exact material bodies through `get_code_snippet` and direct source, call relations through `trace_path`. `ReplayService.prepare` calls dataset pins/read_manifest/inspect and `CandleReplayReader`; callers include `evaluate`, workstation `prepare_replay` and the F22 qualification script. `DatasetService.build` constructs a bound worker request before `_install`. `JobService._accept` decodes the response and validates the admitted artifact rather than trusting provider metadata alone. The shared `Services` protocol exposes execute/cancel/aclose.

Coverage checked every relied-on code/document path. No recorded gaps on these paths; ignored ARCHITECTURE was read directly. Scoped model-worker/forecast coverage reports excluded `__pycache__` only. This signal is best effort, not proof of exhaustive correctness. Broader ingestion and recovery claims above rely on existing tracked documentation/Done status, not a new full source audit.

Current package metadata pins Python `>=3.12.14,<3.13`, uv `0.13.4`, aiohttp `3.14.5`, PyArrow `25.0.1`, AsyncSSH `2.24.0` and optional Textual `8.2.8`; the dev lock contains certifi `2026.10.19`. The aiohttp, certifi and uv updates address confirmed dependency advisories.

## Historical validation, not fresh execution

F24 reports 1,971 portable tests passed / 32 opt-in host skips on its recorded snapshot, separately qualified CPU-worker tests and native installed CLI/service TimesFM artifacts, public Bybit collection/backfill, and post-reboot artifact reopen. It explicitly qualifies snapshot identity and limits; no GPU or broader host profile is established. F23/F22 results are also revision-scoped. This assignment does not repeat the full product gate because it changes documentation and roadmap only.

## Google Drive and historical decisions

The connected [documentation folder](https://drive.google.com/drive/folders/1y__VN96pVbNHKblBSWuxWhmfWY4N83ZO) returned 44 direct children, including raw Markdown copies and seven subfolders. Read `HANDOFF.md` (an empty template) and [F24_VALIDATION.md](https://drive.google.com/file/d/1A_iRbd_FUtLnSy3Ak4Fk6QaGQJhQausy/view), whose content reproduces the older publication-pending checkpoint. The folder also contains duplicate-named F23 validation files. No canonical architecture document appeared among direct children; this is not an exhaustive claim about nested folders. Local Foundation architecture and tracked validation reports retain historical design rationale. The operator subsequently waived Drive updates; no Drive file was created, changed or shared. Repository documents remain canonical and the existing remote snapshots remain intact.

## Unchanged work and authorization

Pre-existing untracked `docs/validation/F01_F20_REVIEW.md` is preserved. No product source, dependency lock, model, host privilege, exchange credential, completed issue status, commit or push is changed by this redesign assignment. New architecture and work items remain proposals/backlog until operator approval.


## Local implementation continuation — 2026-10-21

This dated checkpoint supersedes earlier planning-only statements for the bounded
approved PI00/PI01 continuation. Shared CLI/TUI requests now support
[configurable features-only preparation](../data/ML_PREPARATION.md) over accepted
F18 imports, preserving the original artifacts and adding a source-policy receipt.
[PI01 protection](../architecture/PI01_PROTECTIVE_FOUNDATION.md) adds strict
research envelopes, truthful v1 adapters, an opt-in anchored append-only journal,
dataset/job range checks and committed-job audit reconciliation. Trial/expiry/
intervention/cancellation/restart paths have focused negative/failure tests.

Scientific rules and a future-only reservation are recorded in the exact
[policy](../architecture/PI00_RESEARCH_POLICY_v1.json). Both real sources remain
unadmitted; actual coverage and independent historical availability are unproved.
No training runtime, PI02 labels, real-data research, production enrollment,
financial authority, source download or publication was delivered. New raw builds
in protected research installations deliberately fail closed pending PIT admission.
The policy/profile decisions are not a legal verifier or evidence of predictive
advantage. Current execution/test results and remaining gates are in the
progress record.
