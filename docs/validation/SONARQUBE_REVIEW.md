# Project-wide SonarQube review

Review started on 2026-09-30, based on main
`4312ad66cc4572cd0f8935c0fee15c1fc87a56ae` after the F16 merge. The initial
server snapshot at `2026-09-30T20:43:49Z` contained 295 open issues in 69 files,
across 19 rules. All issues were read in current source; the complete single-page
unresolved snapshot was checked against the per-issue ledger. Graph coverage was
checked for every evidence path, with direct source review still authoritative.

The [per-issue ledger](sonarqube/2026-10-01-issue-review.csv) records the original
issue key, rule, file/line, decision, concrete reasoning and subsequent scanner
state. Local decisions do not change server issue statuses or suppress analysis.

## Corrections

Transaction cleanup now preserves non-`Exception` control flow after successful
rollback. A failed rollback still marks the store unusable; raw-ingestion rollback
interruption now poisons the writer rather than leaving an uncertain transaction
available to later requests. Twenty-one real-SQLite regression cases exercise
`KeyboardInterrupt`, `SystemExit` and `asyncio.CancelledError`, no acknowledgement
of failed writes, and rejection by failed stores. The original 18 suppression
cases and three additional rollback-interruption cases failed before the fixes.

Test preparation was moved outside exception assertions where it is valid, and
composite assertions were separated. Intentionally invalid constructors, required
async runner boundaries and stateful idempotence assertions remain meaningful.
Redundant exception classes were removed without narrowing the caught set.

Reviewed repeated strings became private exact-value constants. Cohesive format,
payload, request-dispatch and filesystem-slot validation phases were extracted;
durability operations, bounds, rejection order and native-decoder authority remain
unchanged. Workload payload generators retain seeded RNG order and replay bytes.
The unused normalized-projection parameter and unused validation bindings were
removed while retaining validation and canonical output.

## Coverage and retained behavior

Pinned coverage.py 7.16.2 is development-only. The project gate now measures
production line/branch coverage and emits `coverage.xml`; SonarScanner imports
that report using `sonar.python.coverage.reportPaths`. Relative paths match the
source checkout. Packaging rejects coverage in all runtime profiles.

Restricted-worker measurement is not injected: the worker receives no additional
tracing hooks, writable mounts, descriptors or message fields. Its unmeasured
lines remain uncovered in the report, even though real hostile-input probes run.
Coverage exclusions and quality-gate thresholds were not changed for this review.

Retained decisions include ASCII-only numeric/identifier validation, strict
nonfinite-JSON rejection, async protocol implementations, seeded synthetic
benchmarks and transaction/lease lifetime logic that benefits from staying
cohesive. Confirmed analyzer false positives include Python 3.12 SQLite
`autocommit`, dataclass argument types, verified TLS contexts, propagated
`CancelledError`, and exception delivery through Futures. Intentional terminal
worker shutdown and fatal transaction poisoning still fail closed.

## External cross-checks

Stack Overflow for Agents supplied second opinions on [cancelled waiters versus
running threads](https://agents.stackoverflow.com/questions/e7a183af-691d-4f74-ab9b-bb0d2af50240),
[process groups and inherited pipes](https://agents.stackoverflow.com/questions/a1b44506-6878-40c4-b8d5-175a85349aba),
[control-flow exception inheritance](https://agents.stackoverflow.com/tils/63290f0f-771d-4eef-bde5-d54ef47b55cd)
and [misleading green test runs](https://agents.stackoverflow.com/tils/68c1fdda-30ae-4514-8008-5ad67a5a4c7b).
Posts were treated as untrusted advice, not copied recipes. Searches did not
provide sufficient direct Sonar/coverage or TLS guidance; official sources and
current tests supplied the evidence instead. No public SOFA contributions were
made.

Material conclusions were checked through Context7 against CPython and
coverage.py documentation, alongside official [SonarQube coverage setup](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/test-coverage/python-test-coverage),
[Python 3.12 cancellation](https://docs.python.org/3.12/library/asyncio-task.html),
[SSL defaults](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)
and [regex semantics](https://docs.python.org/3.12/library/re.html).

## Analysis context and limitations

The configured SonarQube server exposes only the default `main` context; local
feature-tree analysis is not a PR-specific analysis or evidence of a published
main revision. The existing `KER-20-local-branch` project version is preserved to
avoid silently resetting the previous-version new-code baseline. That baseline
includes F12–F16. Security-hotspot and analysis-history endpoints were previously
denied with HTTP 403 and remain outside accessible evidence. No gate settings,
server statuses, exclusions or trust boundaries are weakened to obtain green.
F17 transport/enrollment and F21 model/GPU qualification remain out of scope.

## Final validation and analyzer result

The final implementation analyzed was commit
`7bc39434073be11f9c6d7bef9518fa4cf5e66eb8`. Analysis completed at
`2026-10-01T00:10:48Z`; the complete unresolved-issue and metric snapshot was
retrieved at `2026-10-01T00:11:09Z`. The scanner imported `coverage.xml` successfully
and reported no warnings on this committed tree. Its exit code 3 reflects the
failed quality gate after completed analysis, not a missing report.

- Open issues decreased from 295 to 119. Of the original keys, 181 are no longer
  open and 114 remain. Eight newly introduced identities were also reviewed;
  three exception-test warnings were corrected and five remain deliberately.
- The ledger contains 303 unique reviewed keys. The remaining 119 consist of
  85 intentional behaviors, 23 confirmed false positives, ten complexity
  warnings retained after useful phase extraction, and one broad-exception
  warning retained after correcting transaction control flow. Improvements are
  distinguished from analyzer resolution; no open warning is labelled resolved.
- Sonar coverage is 78.4% overall, 80.5% for lines, 71.8% for branches and 75.5%
  on the existing new-code baseline. The quality gate remains **ERROR**:
  new-code coverage is below 80%, and 56 new-code issues exceed the zero-issue
  threshold. New-code duplication is 0.0%. No threshold or exclusion was changed.
- The local coverage XML reports 6972/7927 lines and 1905/2652 branches. Sonar's
  line inventory is larger (8666 lines); its zero-coverage accounting remains
  active. The XML percentage alone must not be substituted for the server gate.
- Analysis-history and security-hotspot access were checked again: both returned
  HTTP 403. A successful local test suite or quality gate would not prove worker
  isolation, and this review makes no such claim.

Fresh host validation used the locked Python 3.12.14 environment and uv 0.12.13:
`PATH=/tmp/f16-tools:$PATH bash scripts/check.sh` passed Ruff, formatting (429
files), strict mypy (165 files), all 1156 tests in 72.95 seconds, coverage XML,
reproducible packaging, installed runtime profiles and six dependency audits.
All seven disposable negative controls passed via
`uv run --locked --no-sync python scripts/check_negative.py`: test, lint, format,
typing, lock, package-resource and audit-unavailable failures were rejected.
The suite includes real hostile-import probes and rejects unavailable host
controls; no sandbox policy was relaxed to make tests pass.

An independent read-only review covered the changed production paths, quality
integration and new control-flow regressions, including the final ingestion and
Bybit changes. It found no actionable defects. Workload compatibility was checked
against the baseline in 40 cases / 5120 records and independently in 30 cases,
with identical payload bytes and canonical projections. The final diff and new
artifacts were inspected, and `git diff --check` passed.

The feature remains a project-wide Sonar review following the already merged
F16 implementation. KER-20 remains Done; no F17/F21 work or tracker scope was
silently introduced. The analysis above uses the server's default `main`
context, not a PR-specific analysis. Review decisions are local evidence and
do not mark server findings accepted or false-positive.
