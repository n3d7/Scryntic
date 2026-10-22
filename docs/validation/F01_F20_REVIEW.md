# F01–F20 contract review

Reviewed on 2026-10-05 from clean `main` at
`3fdebbe3d19d55f0225240d3cb0056528239141a`, after the F20 PR merge.
Scope: a lightweight cross-check of all twenty task contracts, representative
implementation boundaries and existing regression evidence. This is not an
exhaustive source/security audit or a Foundation v1 release qualification.

## Sources and method

- Read the current top-level Linear issues KER-5–KER-24, including their full
  completion criteria and dependency relations. All twenty are `Done`.
- Compared them with the approved local `ARCHITECTURE.md` F01–F20 rows.
  Historical notes about incomplete F12/F18/F19 were superseded by current
  repository and tracker evidence.
- Inspected implementation entry points, selected call paths in both directions,
  and contract/recovery/security test coverage. Verified material graph claims
  against source; graph inference can misattribute same-named calls.
- Graph generation: `2026-10-05T15:51:42Z`. Exact evidence paths had matching
  metadata and no recorded parse/coverage gaps. Scope exclusions were Python
  bytecode caches. The approved architecture and local runtime document are
  intentionally ignored by Git/indexing and were read directly. These signals
  do not prove source completeness.
- Ran the normal project gates and all existing disposable negative controls.

## Contract cross-check

Each row identifies current implementation and regression evidence considered
in this review. No confirmed implementation blocker was found in this scope.

| Step / Linear issue | Contract and current evidence |
| --- | --- |
| F01 / KER-5 | Exact CPython/uv/build pins and lock inventory; clean wheel/sdist rebuild and installed base/collector/analysis verification. Collector profile has no model dependencies. |
| F02 / KER-6 | Non-mutating lint/format/type/test/lock/package/audit gates; SHA-pinned Actions with read-only token permissions; seven negative controls reject their intended failures. |
| F03 / KER-7 | Application-owned source/provider and domain contracts; identity/decimal/schema/capability and import/type boundary tests in `tests/contracts`. |
| F04 / KER-8 | Validated configuration precedence and protected paths; scoped file credentials, public zero-resolution and sentinel-redaction cases in `tests/configuration`. |
| F05 / KER-9 | Single-owner SQLite writer, WAL/FULL and commit-before-acknowledgment; abrupt-exit and post-startup writer-failure regressions in `tests/ingestion`. Writer failure fails active/queued Futures and rejects later admission. |
| F06 / KER-10 | Normalization outcome/checkpoint/barrier changes share an explicit transaction; duplicate/conflict/finality/provenance and process-kill recovery tests in `tests/normalization`. |
| F07 / KER-11 | Immutable raw/normalized Parquet objects, canonical manifest chain and recoverable pending publication; archive round trips and publication recovery tests. |
| F08 / KER-12 | Deterministic exact-manifest candle selection and immutable dataset/provenance; missing-object, revision/conflict and unsupported-schema fixtures in `tests/dataset`. |
| F09 / KER-13 | Disk-backed source-to-model composition and bounded validated artifacts; `tests/slice/test_end_to_end.py` repeats after restart and swaps the two trusted fake providers. |
| F10 / KER-14 | Committed recorded/synthetic workloads and four result inventories; measured Parquet/framed-zlib comparison, selected production codec and configurable 90-day sizing in `docs/data/performance/f10-operating-envelope.md`. Existing benchmark conformance tests pass. |
| F11 / KER-15 | Publication reconciliation, no-replace installation and transaction boundaries; fsync/crash/conflicting-history/competing-publisher/prior-codec tests in `tests/publication` and `tests/archive`. |
| F12 / KER-16 | Read-only chrony adapter; wall/monotonic/boottime continuity and epochs; fresh uncertainty-aware finality and observation cutoffs. Clock-step/stale/missing/suspend/restart/ambiguous-boundary tests pass. |
| F13 / KER-17 | Allowlisted public REST, bounded category-aware pagination and query-bound persisted cursors; ordering/overlap/finality/unit/category/restart fixtures in `tests/sources`. |
| F14 / KER-18 | Allowlisted TLS WebSockets, acknowledged subscriptions, bounded frames/intake, heartbeat/reconnect and joined cancellation; shared normalization/archive/dataset integration and local-server regressions. |
| F15 / KER-19 | Durable coverage/loss/checkpoints, shared live/backfill budget and family-specific recovery; absent loss markers remain conservatively unknown. Pressure/repair/restart/repeated-cancellation tests in `tests/recovery`. |
| F16 / KER-20 | Hostile import staging/catalog and fixed restricted decoder; native parsing stays outside the coordinator. Corrupt/path/race/oversize/timeout/protocol and effective host-control probes in `tests/imports`. |
| F17 / KER-21 | Pinned-host read-only SFTP, isolated transfer credentials, bounded packets/operations and durable anchors; repeated/interrupted pulls, conflicting history, bootstrap/backup/restore and cancellation in `tests/sync`. |
| F18 / KER-22 | Restricted dataset build/inspection/export, revision/time/coverage recipes and immutable provenance/pins; imported old/new schemas and forged/tampered worker results in `tests/dataset`. |
| F19 / KER-23 | Offline deterministic replay, temporal splits/purging, train-only transforms and per-series baseline metrics; future-data/leakage/quality/gap/report-repeatability tests in `tests/replay`. |
| F20 / KER-24 | Exact provider/model/use/loading admission, versioned bounded contracts and durable jobs; cancellation/deadlines/restart fencing and hostile loopback HTTP response tests in `tests/jobs`. Remote execution defaults to disabled. |

## Current validation

The host's `uv 0.12.19` correctly failed the project's `==0.12.13` requirement
before running gates. Provisioned an isolated uv 0.12.13 executable under
`/tmp/scryntic-f01-f20-review-20261005/bin` from the public wheel recorded in
`docs/operations/RUNTIME.md`; verified both wheel and executable SHA-256 inventory entries.
The global uv installation and project pins were not changed.

With that directory prepended to `PATH`:

| Check | Result |
| --- | --- |
| `bash scripts/check.sh` | PASS: locked sync, Ruff, formatting, strict mypy, coverage/pytest, packaging/profile integrity and all six dependency audit profiles. |
| Full pytest under coverage | **1,518 passed in 96.00 seconds**. |
| `uv run --locked --no-sync python scripts/check_negative.py` | PASS: test, lint, format, type, stale-lock, missing-resource and unavailable-audit negative controls; source inputs unchanged. |
| `git diff --check` | PASS for the tracked documentation changes; the new review's twenty-row inventory and evidence paths were checked separately. |
| Existing SonarQube `Scryntic` / `main` snapshot | Gate **OK**, new-code coverage **89.2%**, duplication **0.0%**, **0 OPEN/CONFIRMED issues**, **0 TO_REVIEW Hotspots**. |

SonarQube's last analysis is **2026-10-05 15:23:17 UTC**, the committed F20
analysis preceding the merge. Current production, tests, dependency and gate
inputs match analyzed F20 commit `7c7fee491df0a70b249ed15cf87c0bc9907102ff`.
This review read the existing server snapshot; it did not run a new Sonar,
Semgrep or Trivy analysis. The fresh project dependency audit found no known
vulnerabilities; it does not qualify OS/kernel/model artifacts.

## Corrections

- Aligned `docs/validation/QUALITY.md` with the existing setup-uv v10.2.0 workflow pin,
  corroborated by its [official release](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0).
- Replaced the obsolete F18-future wording in `docs/data/RECOVERY.md` with the current
  explicit coverage-capture boundary.
- Added this review and its documentation-index link. No runtime behavior,
  architecture, dependencies, exclusions, rules or gate thresholds changed.

## Remaining qualification limits

- Current model execution is trusted synthetic fixtures. F21 owns model-worker
  isolation qualification; F22 owns one approved real-model integration and
  evaluation. The existing closed loading policy intentionally rejects unknown
  or unsafe real-model requirements until a qualified profile is implemented.
- F12 host evidence is qualified for the documented Linux/chrony profile. The
  recorded F14 public probe accepted eight envelopes and reached a dataset,
  but had **zero finalized rows** because host timing evidence was stale.
  This is correct fail-closed behavior, not evidence of healthy real-host
  finality. Public capture, host synchronization probes and long-running soak
  were not repeated in this lightweight review.
- Publication v1.0 does not transport the F15 recovery ledger. F18 retains
  explicitly supplied trusted coverage captures; automatic service composition
  remains separate work. Unknown coverage must not be relabeled complete.
- F10 measurements retain their original host/workload/filesystem scope. The
  million-envelope benchmark was not rerun, and its rates are not an end-to-end
  service or forecasting-performance guarantee.
- F20 guarantees at-most-once accepted success, not exactly-once external
  computation. Its loopback HTTP double does not qualify an arbitrary remote
  endpoint, cloud deployment or credentials.
- CLI/daemon/operator/pruning/release-soak work remains in the later tasks.
  This review establishes no real-model forecasting advantage or trading
  authority and does not qualify Foundation v1 for release.

Linear was read-only throughout this review. Documentation edits remain local;
no branch, commit, push, PR or tracker mutation was performed.
