# F18 / KER-22 validation

Implemented from main `d4ba958074289011cceffd56685d6ba882a00605`, with a clean
initial working tree, on `ker-22-f18-dataset-provenance`. Scope is
[KER-22](https://linear.app/kerigan/issue/KER-22/f18-complete-datasetprovenance-and-schema-evolution)
and the approved F08/F12/F15/F17 contracts. Architecture, dependencies, lockfile,
security boundaries and Sonar settings are unchanged. See [DATASETS.md](DATASETS.md).

## Acceptance evidence

| Contract | Evidence |
| --- | --- |
| Exact accepted values/provenance | Real publication → ImportCatalog → restricted build/inspect/export; repeated builds return the same reference; fingerprints and original selected projections match independently constructed inputs; reopen preserves rows/receipts/pins |
| Revision and cutoff policy | First/latest open-revision/finalization, duplicates and known conflicts; late/overlapping uncertainty excluded even within budget; broken eligible history fails closed |
| Coverage/time quality | Unknown remains unknown; include/exclude/require policies; partial/overlapping coverage and late repair fixtures; capture/hash retained in provenance/pins |
| Trusted features/labels | Exact contiguous same-instrument lag/future closes, finalized intermediate labels, explicit nulls/reasons on gaps, independent source fingerprints; no mutation/imputation |
| Compatibility | Recipe 1.0/1.1 and old dataset 1.0 refs remain supported; recipe 1.2/dataset 1.1 are explicit; unknown major/newer minor and unsupported imports fail |
| Restricted decoding | Real Linux worker on every build/reread/export; fresh coordinator subprocess denies PyArrow imports and still completes all operations; imported 1.0 outputs cannot use legacy coordinator reader |
| Immutable provenance/pins | Exact accepted body/descriptors independently compared; forged worker provenance/untyped rows rejected; source/output tampering rejected; explicit retirement deletes only pin |
| Durability | No-replace files plus fsync; independent review found missing parent sync on first pin-directory creation, repaired and verified with parent-before-pin ordering in a real first build |

## Local gates, 2026-10-03

The host has uv 0.12.19; the project requires 0.12.13. Exact uv 0.12.13 was obtained
in `/tmp/f18-tools`, without replacing host tooling. Locked environments use
CPython 3.12.14 and PyArrow 25.0.1. External Codex sandboxing prevents Bubblewrap
NETLINK_ROUTE and SQLite WAL operations; the authorized tests ran outside that
outer sandbox with the application's actual Bubblewrap/libseccomp controls intact.
No isolation control or test was disabled.

```sh
PATH=/tmp/f18-tools:$PATH bash scripts/check.sh
PATH=/tmp/f18-tools:$PATH uv run --locked --no-sync python scripts/check_negative.py
uv run --locked --no-sync pytest tests/dataset -q --tb=short
uv run --locked --no-sync ruff check .
uv run --locked --no-sync ruff format --check .
uv run --locked --no-sync mypy --no-incremental
git diff --check
```

- Normal gates passed: lock/sync, Ruff, strict mypy (188 source files), **1261
  tests**, coverage XML, reproducible sdist/wheel rebuild and base/collector/
  analysis installed-profile checks, dependency audits with no known vulnerabilities.
- All seven negative controls passed: tests/lint/format/types/stale lock/missing
  resource/unavailable audit fail nonzero while preserving inputs.
- After the final Sonar exception-test clarity repairs, all **39 dataset tests**,
  project Ruff/format/mypy checks passed again. Production sources were unchanged
  from the final full gate run; packaging/audit evidence remains applicable.
- Final production coverage XML: 8411/9670 lines, 2313/3250 branches. Restricted
  worker coverage is not instrumented by injecting tracing hooks or capabilities.
- Independent source review and scoped re-review found no remaining material
  defects. Review did not include power-loss/crash simulation; fsync ordering and
  no-replace behavior are tested, not a claim of hardware immunity.

## SonarQube review

SonarScanner CLI 8.0.1.6346 against Community Build 26.9.0.129388 analyzed the full
current feature tree under the existing `Scryntic` project's default `main`
context, with version label `KER-22-F18-local-branch`. This is not GitHub-main or
PR-specific analysis. Full source indexing preserves the existing baseline;
review and repair were filtered to 12 affected/new production files and four new
test files, with complete result pages. No global baseline cleanup, issue-status
writes, threshold change or exclusion change was performed.

The [per-issue ledger](sonarqube/F18-review.csv) records 20 F18-attributable identities
no longer open: cohesive phase extraction and constants/conditions; verified
exact-type-narrowing analyzer false positives clarified with explicit casts; and
nine exception tests with preparation moved outside `raises`. Existing assertions
were retained and the legacy exception assertion now names DatasetBuildError.
Four older findings remain on changed production paths: two baseline complexity
warnings, intentional terminal worker shutdown, and the known Python 3.12 SQLite
`autocommit` false positive. They were verified against actual source/tests and
not expanded into this task.

Initial implementation committed-tree analysis at **2026-10-03T11:16:44Z**, revision
`fd163b906ee79f7bfb9a3d081503e2fae7056930`, imported `coverage.xml`
without a coverage warning. Production query: four pre-existing findings; new-test
query: zero findings; new-code issue metric: **0**. That analysis's gate was **ERROR** solely
because new-code coverage is **78.5%**, below 80%; new-code duplication is **0.0%**.
Scanner exit 3 means the completed analysis failed that gate, not an absent report.
The committed-tree scan completed SCM blame for all 16 changed source/test files;
the subsequent documentation-only commit does not change analyzed code. Coverage
percentages are server baseline metrics, not
proof that restricted parsing is safe or that worker results cannot be malicious.

## Coverage follow-up after PR #20, 2026-10-03

After the implementation was merged as `fe831404623dbcc6ba38b46409fa2ac65d1f193c`,
the requested coverage repair was developed on `ker-22-f18-coverage`. It changes
tests and documentation only. It adds **78** checks for closed canonical IPC,
non-coerced worker values, invalid policies/cutoffs/coverage captures, refusal
before input/native access, independent worker-result identity/cardinality checks,
and corrupted-pin read/recreate/retirement failures. No production code, threshold,
exclusion, coverage configuration or restricted-worker capability changed.

The normal project gates passed with **1339 tests**, strict mypy (191 files),
Ruff/format, reproducible packaging and installed-profile checks, and dependency
audits. Sonar reported one composite assertion in a new test (S9073); splitting it
retained both checks. After that repair, all **117 dataset tests** and project
Ruff/format/mypy checks passed again. The production coverage report remains valid:
8453/9670 lines and 2350/3250 branches; `protocol`, `recipes`, `service` and `pins`
each have 100% measured line/branch coverage. Native worker instrumentation remains
absent and the real restricted build/reread tests remain enabled.

Committed-tree Sonar analysis at **2026-10-03T12:54:00Z**, revision `3acb43f`,
completed successfully (scanner exit 0): **gate OK**, new-code coverage **81.8%**
(required 80%), **0 new issues**, **0.0% new duplication**. The three new test files
have no open/confirmed findings. The original four baseline findings remain;
the additional repaired test finding is recorded in the issue ledger. The server
still uses its default `main` analysis context for this local feature tree; this is
not a scan of GitHub main or a PR-specific server analysis. The later
documentation-only commit does not change analyzed code.

## Remaining limits

- Publication 1.0 does not transfer the F15 recovery ledger. Explicit trusted
  captures are supported; absent coverage stays unknown. As-observed chronology is
  the collector's receipt claim, with workstation import receipt recorded separately.
- Input/output ceilings are explicit and fail closed. These tests do not qualify
  larger workloads, other host/codec/schema profiles or GPU execution.
- Sonar coverage now passes with unchanged settings. Native-worker coverage remains
  uninstrumented; passing metrics do not establish parser security or qualify new
  operating profiles.
- CLI/replay/train-only fitting and pruning are F24/F19/F29 work. Pins expose exact
  retention roots; F18 performs no artifact deletion or automatic retention.

Implementation agents leave KER-22 **In Review**, never Done. The implementation
branch is committed/pushed and a PR targets main; it must not be merged by this task.
