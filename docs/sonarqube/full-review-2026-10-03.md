# Full-project SonarQube review — 2026-10-03

The figures below preserve the historical repair-stage snapshot. The
[2026-10-04 follow-up](closure-2026-10-04.md) revalidated the current main repairs
and records authorized server dispositions and a repeated full analysis with
zero open issues. Use that follow-up for the current server state.

This review supersedes the earlier F18-scoped review and October 1 server
snapshot. The replacement server's fresh full analysis is authoritative;
older artifacts remain historical records.

The repository started clean on `main` at
`131b7e9a6e4feeac62cce9d6f17498dda14afbe3`. Repairs are local, uncommitted changes.
Scanner SCM metadata therefore names the base commit, not a published repair.
SonarScanner CLI 8.0.1.6346 analyzed the root with the unchanged
`sonar-project.properties` and `sonar.qualitygate.wait=true`, against SonarQube
Community Build 26.9.0.129388. Package version remained `0.1.0.dev0`.

## Evidence

The [analysis record](full-review-2026-10-03.json) includes every analysis date,
metrics, gate conditions, hotspots, duplication and final uncovered paths.
The [complete issue ledger](full-review-2026-10-03.csv) records every observed key,
location, decision and rationale, including findings introduced and repaired
during the loop. Final figures follow the last full scan.

| Measure | Initial 16:47:44 UTC | Final 17:52:05 UTC |
| --- | ---: | ---: |
| Open issues | 133 | 40 |
| Confirmed actionable issues remaining | Reviewed during repair | 0 |
| Overall coverage | 79.1% | 80.3% |
| Line coverage | 81.2% | 83.8% |
| Branch coverage | 72.3% | 69.7% |
| New-code coverage | Not evaluated | 85.4% |
| Duplicated lines / blocks / files | 97 / 6 / 2 | 0 / 0 / 0 |
| Duplication density | 0.5% | 0.0% |
| Security Hotspots | 0 | 0 |
| Technical debt estimate | 1051 minutes | 325 minutes |
| Quality Gate | OK; no evaluated conditions | ERROR; 2 reviewed new findings |

Final gate conditions: new coverage 85.4% passes the unchanged 80% threshold;
new duplication 0.0% passes the unchanged 3% threshold; two new findings fail the
unchanged zero-new-issues threshold. Server legacy bug/vulnerability counts remain
5/3 and ratings E/D because reviewed findings still have OPEN status. These are
scanner classifications, not independently confirmed defects.

The final 40 findings comprise 11 dataclass type-model reports, 11 async contract
reports, six control-flow reports, four exact-dict narrowing reports, three
supported autocommit reports, two cancellation reports, two TLS reports and one
benchmark PRNG report. All have per-key rationale in the CSV. No complexity,
ambiguous exception-test, JSON callback or unjustified duplication finding remains.

Complete issue pages came through the official read-only SonarQube MCP; all
results fit in one 500-item page. All hotspot results were empty. Every file
below 100% coverage had its uncovered line/condition details retrieved. The
remaining files were fully covered. All initial duplicated blocks were inspected;
final duplicate measures and file queries agree on zero. Manual inspection
followed findings and affected call paths, rather than an unrelated repository
audit.

## Repairs

- Split complex archive, dataset selection/provenance, import IPC, ingestion,
  normalization, publication, transport, clock, configuration and benchmark
  functions into cohesive stages. Existing tests govern exact references/hashes,
  validation order, revision transitions, temporal cutoffs and restricted decoding.
- Shared private SQLite opening, pinned connection cleanup and schema comparison
  in `scryntic.sqlite_state`. Domain transactions, locks, checkpoint advancement,
  commit/readback and rollback remain separate. Recovery now poisons the writer
  when rollback returns with the transaction open; its regression fails without
  this postcondition.
  Inode-replacement failures retain the original domain exception class and
  diagnostic message; the neutral helper accepts the caller's fixed domain error.
- Simplified regexes using explicit or preserved `re.ASCII`; Unicode regression
  cases prove acceptance was not widened. Replaced generator-throw JSON callbacks
  with explicit rejecting callbacks.
- Clarified reservation idempotence and exception assertions. Preparation, attack
  setup and cleanup no longer satisfy assertions intended for a later operation.
  Staging verifies raw inode rejection and public exception translation separately.
- Measured owned Python scripts alongside the package, matching existing Sonar
  scope. Added actual audit-profile orchestration, bounded archive corruption and
  journal measurement, SQLite file/pin, rollback, TLS context, ASCII and cancellation
  tests. No tracing was injected into restricted workers.

No thresholds, severities, profiles, Sonar exclusions, suppressions or server
dispositions changed. Final diff review removed a redundant `isinstance` proposed
solely for analyzer narrowing; the exact-dict precondition remains sufficient.

## Remaining findings

Every remaining key has concrete evidence in the ledger:

- SQLite `autocommit=True` is supported by pinned Python 3.12. Removing it changes
  explicit SQL transaction control. [Python SQLite documentation](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.connect).
- `dataclasses.replace` returns the same `CoverageSnapshot`/`Enrollment` type as
  the annotated callees require. Runtime probes, persisted transition tests and
  strict mypy support the false-positive decisions. [Python dataclass documentation](https://docs.python.org/3.12/library/dataclasses.html#dataclasses.replace).
- Both actual connectors use `ssl.create_default_context()` with certificate and
  hostname verification. Unit tests inspect effective contexts; no real exchange
  handshake is claimed. [Python TLS documentation](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context).
- Seeded PRNG use creates reproducible public benchmark inputs/read samples,
  without credentials, tokens, keys or security decisions. Workload tests verify
  repeatability and segment identities. [Python random documentation](https://docs.python.org/3.12/library/random.html).
- Immediately completing async implementations preserve archive/source/provider
  ports. Artificial scheduling or removing `async` distorts these contracts.
- BaseException handlers transport exceptions through a Future, join ownership
  cleanup before rethrowing, poison a damaged writer, close a failed catalog or
  terminate a partially isolated worker. Narrowing these weakens their boundaries.
- Recovery joins shielded durable work before propagating caller cancellation.
  Stream close consumes only cancellation of source-owned child work when the
  owner has no pending cancellation; explicit owner cancellation is reraised.
  Deterministic tests verify both paths. [Python cancellation documentation](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation).
- Exact-dict preconditions reject non-dicts and subclasses before subscription.
  Analyzer narrowing complaints are checked against source, normalization tests
  and strict mypy; no redundant guards or casts are kept to hide them.

The final gate must be read from actual server conditions. A completed analysis
can return exit 3 because `new_violations` counts the two new reviewed constructs:
supported `autocommit` in `sqlite_state.py`, and the source-owned cancellation
boundary in `bybit_live.py`. Source behavior must not be distorted to evade these
rules. MCP is read-only; findings therefore remain open until an authorized server
review changes their status. This differs from remaining actionable code work.

## Validation and limits

Normal command: `PATH=/tmp/f18-tools:$PATH bash scripts/check.sh`, selecting pinned
uv 0.12.13. It checks locked sync, Ruff, strict mypy, all tests and fresh XML,
reproducible wheel/sdist and installed profiles, and exact dependency audits.
Final command results are recorded in the JSON.
The exact final-tree run passed **1365 tests in 88.79 seconds**, Ruff lint and
formatting (471 Python files), strict mypy (199 files), packaging/installed-profile
checks and all six dependency-audit profiles. Focused suites passed during each
repair; the rollback regression was additionally verified red without its
postcondition and green with it. A publication-time runtime probe accepted valid
dict/None inputs and rejected 11 invalid or subclass inputs as ValueError, without
any subscription TypeError.

`uv run --locked --no-sync python scripts/check_negative.py` passed seven controls:
tests, lint, formatting, types, stale lock, missing package resource and unavailable
audit service. Each rejected its fault without changing temporary inputs. Host
execution was necessary for namespace, loopback and SQLite WAL qualification;
application worker isolation remained enabled.

Restricted workers/bootstrap/probes remain untraced and uncovered; operational
capture/install scripts and subprocess benchmark orchestration have uncovered
paths listed in the JSON. These are measurement limits, not new exclusions.
Script measurement exposes its branch denominator, so the final branch percentage
is not directly comparable to package-only measurement. This review makes no
comprehensive security audit or live-market integration claim. No tracker update,
commit, push, PR or merge was performed.

Context7 and primary coverage.py documentation informed measurement scope and
relative XML paths. A selective read-only SOFA lookup for generator cleanup
pitfalls returned no directly applicable guidance; none was adopted. ColGrep was
attempted for PRNG intent, but its index process was killed (137); graph evidence,
exact source and tests supplied verification instead.
