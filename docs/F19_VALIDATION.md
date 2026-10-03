# F19 / KER-23 validation

Implemented from current main `131b7e9a6e4feeac62cce9d6f17498dda14afbe3`, verified
equal to fetched origin/main, on `ker-23-f19-replay`. Existing local changes were
preserved in the original checkout; implementation uses `/tmp/scryntic-f19`.
Scope is [KER-23](https://linear.app/kerigan/issue/KER-23/f19-add-deterministic-replay-and-baseline-evaluation),
the approved architecture and completed F18 contracts. No runtime dependencies,
lock, gates, native-worker capabilities or provider/execution boundaries changed.
See [REPLAY.md](REPLAY.md) and [F19_PLAN.md](F19_PLAN.md).

## Acceptance evidence

| Contract | Evidence |
| --- | --- |
| Deterministic clock/readers | Monotone synthetic clock; exact fixed nanoseconds; feature-only reader protocol; repeated runs produce identical results and canonical report SHA-256, including fresh coordinator process |
| Future-data exclusion | Late backfill/selected data excluded strictly; historical mode explicitly differs; observation uncertainty must precede earliest decision; a late past lag cannot become a feature; labels unavailable until their complete chain is observable |
| Temporal splits and label purge | Absolute half-open boundaries; decisions crossing uncertainty excluded; multistep overlapping labels purged on train/validation and validation/test boundaries; late training labels never enter fitting; labels already known at origin are excluded |
| Train-only transforms | Recorded eligible training rows only; future values cannot alter fit or past predictions; frozen per-series population standardization; constant columns scale 1; no-training/empty cases explicit |
| Simple baseline and metrics | Last available close, no prediction parameters/randomness; exact MAE/RMSE/signed bias in price units per series/split; multiple series never pooled |
| Honest quality/gap accounting | Unknown coverage remains unknown; later repair cannot change a past strict decision; unhealthy timing is flagged or excluded; labels/dependencies obey policies; original F18 exclusions and partial/missing derivation lineage retained |
| Restricted inputs and pins | Real publication/import/F18 build/inspection; active pins required and unchanged; retired pins refused; fresh coordinator denies PyArrow/live-source/provider imports and still reproduces the report; malformed lineage and amplified decimal exponents refused |
| Bounded reproducible report | Canonical immutable bytes/hash; full F18 provenance/Parquet/dataset hashes and pins; config, code/runtime/lock, fit state, forecasts/labels/metrics/exclusions; 10,000-row and 64 MiB report ceilings |

Independent source review found one valid open-lag case: F18 permits an open lag,
but F19's chosen finalized-candle baseline must not silently consume it. A failing
fixture confirmed the case; `lag-not-finalized` now excludes it without changing
F18 derivation. Scoped re-review found no material regression in that fix or the
subsequent cohesive chain/forecast phase extraction. The reviewer independently
passed 42 pure tests initially and 13 affected tests after repair; integration
verification uses the parent's qualified execution below.

## Local gates, 2026-10-03

Host uv is 0.12.19; project uv 0.12.13 was used from `/tmp/f18-tools` without replacing
host tooling. The locked environment uses CPython 3.12.14 and PyArrow 25.0.1.
Outer Codex sandbox constraints require authorized escalation for SQLite,
Bubblewrap/namespaces and network-backed packaging/audit; application isolation
controls remain active. No test or boundary was disabled.

```sh
UV_CACHE_DIR=/tmp/f19-uv-cache PATH=/tmp/f18-tools:$PATH bash scripts/check.sh
UV_CACHE_DIR=/tmp/f19-uv-cache PATH=/tmp/f18-tools:$PATH \
  uv run --locked --no-sync python scripts/check_negative.py
UV_CACHE_DIR=/tmp/f19-uv-cache /tmp/f18-tools/uv run --locked --no-sync pytest -q tests/replay
UV_CACHE_DIR=/tmp/f19-uv-cache /tmp/f18-tools/uv run --locked --no-sync \
  coverage report --include='src/scryntic/replay/*' -m
git diff --check
```

- Final normal gates exited 0: locked sync, Ruff/format, strict mypy (**205 source
  files**), **1385 tests**, coverage XML, reproducible sdist/wheel rebuild and
  base/collector/analysis installed-profile verification, dependency audits with
  no known vulnerabilities. Focused F19 suite: **46 tests**.
- All **seven negative controls** passed with nonzero rejection and unchanged
  inputs. The initial offline resource control lacked a populated build cache
  when run concurrently with packaging; after normal packaging populated it, the
  unchanged control reached the intended missing-py.typed rejection and passed.
- F19 measured line/branch coverage: **97%** (400 statements, 134 branches;
  9 uncovered statements and 9 partial branches). Contracts and service are
  **100%**, reader **95%**, evaluation **96%**. Native restricted-worker coverage
  remains uninstrumented, exactly as in F18.
- After the last assertion-order-only Sonar clarification, all **7 evaluation
  tests** passed again; production and coverage inputs were unchanged.
- `git diff --check` passed; the original checkout's porcelain status matches
  its saved baseline. Branch/commit/publication verification follows below.

## SonarQube

Full analysis uses the existing `Scryntic` project/configuration, Community Build's
default `main` server context, version label `KER-23-F19-local-branch`. This is a
local feature-tree scan, not GitHub-main or PR-specific analysis. Native-worker
tracing, gates, thresholds, exclusions, severities and server dispositions remain
unchanged. The [issue ledger](sonarqube/F19-review.csv) records all 13 attributable
issues reviewed/repaired: two complexity extractions, one consistent assertion
order, two composite assertions and eight exception-test preparation clarifications.
Original assertions and failure behavior remain intact.

Initial completed analysis at 2026-10-03T20:26:03Z: gate ERROR due solely to 13 new
issues; new coverage 96.6%, new duplication 0.0%, scoped Security Hotspots 0.
The next full analysis cleared 12. Exact server flow locations identified the
remaining assertion-order issue: the analyzer classified `(manifest, rows)` as
expected despite both being mutated-input actuals. Separate manifest/row equality
checks clarify the classification while preserving both immutability assertions.
Final local-tree analysis at **2026-10-03T20:35:24Z** completed with scanner exit 0:
**gate OK**, **0 new issues**, **96.6% new-code coverage**, **0.0% new-code
duplication**, **0 scoped Security Hotspots**. Complete result pages were inspected
through SonarQube MCP. Four production files and all new test files have no remaining
attributable findings. Initial untracked-file SCM warnings will be resolved by the
committed-tree verification before publication evidence is finalized.

## Remaining limits

- As-observed retains F12 collector receipt trust assumptions; synthetic decisions
  do not prove physical or workstation possession time. A fixed selected snapshot
  excludes unavailable later revisions; it does not recover earlier values.
- Publication 1.0 does not transport the F15 recovery ledger. Explicit captured
  claims are supported; absent evidence remains unknown and default policies exclude.
- F18 inspection revalidates each 128-row page; maximum-size replay throughput is
  not benchmarked. No new operating/GPU/parser profile is qualified.
- Source/output retention is required for reproduction. This task changes no pins,
  pruning policy or CLI destination handling. No real model/provider comparison or
  trading simulation is included; F20/F22/F24 remain separate approved tasks.
- No forecasting advantage is promised. Passing coverage/gates does not prove
  worker numerical honesty, source clock truth or hardware crash immunity.

KER-23 is left **In Review**, never Done. The PR targets main and is not merged.
