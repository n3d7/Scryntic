# PI02 historical feasibility validation

## Scope and provenance

PI02 uses the registered PI00 v2 policy (`d75c7c0278d011b38c5e2bfaff151e7f3071b56edca9dce3d1d883294f3a14b8`) and the frozen [PI02 feasibility spec](../architecture/PI02_FEASIBILITY_SPEC_v1.json) (`5624726a3ba868579da0fa26ab125fbe8df3eb3bcd0189723725a9951ec8e0f3`). The only admitted real input is the previously retained Binance Vision USD-M BTCUSDT and ETHUSDT 1h monthly archive set from 2020-01-01 through 2026-10-01. Each instrument has 81 monthly archives and 59,160 hourly rows. The run verifies the registered archive digests, sidecars, row bounds, and an active PI01 final-holdout reservation before calculating counts.

This input is **historical reconstruction**: its original first-seen time is unknown. The run did not fit a model or access final-holdout labels. It does not establish live as-of availability, predictive performance, or a final-test result.

## Registered counts

The independent Debian run on 2026-10-22 matched the earlier local run exactly (`SHA256` of the canonical counts object: `2266d93ca76becef45e8b91fec75b91f2871744211d87625bdba7a2875338455`). Counts below apply separately to **each** instrument; the two instruments have identical counts. Each cell is matured origins / nonoverlapping horizon blocks.

| Split | 1h | 1d | 7d | 28d |
| --- | ---: | ---: | ---: | ---: |
| Development | 43,823 / 1,826 | 1,825 / 1,825 | 1,819 / 260 | 1,798 / 65 |
| Calibration | 8,087 / 337 | 336 / 336 | 330 / 48 | 309 / 12 |
| Exploratory test | 5,879 / 245 | 244 / 244 | 238 / 34 | 217 / 8 |

All structurally eligible origins were mature and complete in this frozen historical input; no data-gap exclusions occurred. Structural exclusions still apply at split boundaries and for the registered 28-day embargo. All four durations are expressible. The exploratory independent-block floor is met for 1h and 1d, but **not** for 7d (34 versus 60) or 28d (8 versus 40). The latter two durations cannot support the registered claim under this split and source window.

The Debian process used 93,216,768 bytes peak RSS and 16.038 seconds of wall time, within the registered 4 GiB and 180-second caps. The result and raw archives remain in private lab evidence, outside the repository.

## Implementation checks

- PI01's default-deny `build` admission remains in force. PI02 has a separate, strict development-input admission that checks the registered policy, source, snapshot, UTC bounds, and zero-label input, and records an audit event.
- Finalized 1h bars resample into complete UTC buckets without forward filling. Source lineage and revision selection are deterministic. Origin/input identifiers are independent of future outcomes; outcome versions are separate and refer to the future raw lineage.
- Focused research, dataset, and replay tests passed. The full Debian `scripts/check.sh` gate passed: Ruff, formatting, mypy, 2,027 tests passed with 32 skipped, package check, and dependency audits. The gate was run with `umask 022`, required by the existing secure-path tests.
- Trivy 0.75.0 scanned both lockfiles and the source snapshot for vulnerabilities, misconfigurations, and secrets at all severities: zero findings. Semgrep 1.179.0 with the pinned Python rules scanned the nine changed Python files: zero findings and zero scan errors.
- Full SonarQube analysis on the private Debian server used a separate `Scryntic-PI02` project because Community Build does not support branch analysis. Its default Sonar way quality gate passed on 2026-10-22; coverage was 81.6% and duplication 0.0%. Eight open issues in PI02 files concern function complexity and test assertion form. Sonar reported no vulnerability in PI02 files. Eight project-wide vulnerability issues were in unchanged modules. This analysis is of the local PI02 source snapshot, not a remote PR build.

## Boundary for later tasks

PI03 may use the origin/input and outcome version contracts, lineage, and feasibility result as inputs to a separately approved model experiment. PI04 may use the split and claim-gate results for a separately approved evaluation. Neither task has been implemented here. The historical first-seen limitation and the 7d/28d claim-floor failures must remain visible in any later decision; PI02 does not widen the registered policy or admit final-holdout data.
