# PI03 chronological baseline validation

## Scope and registration

KER-63 executes the fixed [PI03 specification](../architecture/PI03_BASELINE_SPEC_v1.json) and [pre-fit contract](../architecture/PI03_PREFIT_CONTRACT.md). The contract and actual [per-interval counts](../architecture/PI03_PREFIT_COUNTS_v1.json) were recorded before the first fit. PI00 and PI02 no-fitting artifacts are unchanged; PI03 has a separate typed admission.

Five configurations completed on the private Debian CPU launcher on 2026-10-24: zero return, training-only historical mean, ridge AR(1), ridge AR(24), and volatility-scaled empirical returns. All five results passed immutable identity, attempt-fence, effective-control and independent prediction/score recomputation checks. The same PI01 installation recorded **five physical starts out of eight allowed**, including any failures; there were no failed actual fits or reclaimed attempts. The ledger is installation-scoped under PI01's trusted-operator boundary. Changing or resetting installations is not an authorized way to recover budget.

The admitted input SHA256 is `bf30c207a6e952cc3a033b053ec4ac651f69cd42a743e3172dc8e79e99c1aff3`; raw specification SHA256 is `2ba095a3763b16a28b6c58b30b2f37f42bdeca1cbf669beb43a79ce5963cd099`. It projects only the registered BTCUSDT/ETHUSDT hourly development history. Training origins are `[2020-01-02, 2021-08-01)` UTC and evaluation origins `[2021-09-01, 2025-01-01)`. Training outcomes mature strictly before the fit cutoff, evaluation outcomes before their end, with the registered observation delay, interval purge and at least 28-day embargo. Preprocessing and dependence estimates use training data only.

This is **historical reconstruction** with unknown original first-seen/revision times. It does not establish live as-of availability. The reserved final interval `[2026-11-01, 2030-01-01)` remained closed; no final labels, exchange credentials, financial execution authority, GPU training or PI04 functionality were used.

## Counts and descriptive results

Counts apply separately to each asset and identically to every algorithm. Assets do not multiply independent time blocks. There were no data-gap exclusions; the reported exclusions are immature or boundary-touching labels, plus one missing initial feature history in training.

| Horizon | Training origins per asset | Evaluation origins per asset | Evaluation boundary exclusions | Adjusted time-block upper bound | Required floor |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1h | 13,846 | 29,231 | 1 | 17 | 240 |
| 1d | 575 | 1,217 | 1 | 17 | 120 |
| 7d | 569 | 1,211 | 7 | 17 | 60 |
| 28d | 548 | 1,190 | 28 | 17 | 40 |

The registered training-only dependence diagnostic produced **73 days**. All independent-block claim floors therefore failed before fitting. These counts are upper bounds, not proof of independence or statistical power.

The table reports return MAE, with equal weight for BTC and ETH, in raw log-return units. These are descriptive development estimates.

| Algorithm | 1h MAE | 1d MAE | 7d MAE | 28d MAE |
| --- | ---: | ---: | ---: | ---: |
| Zero | 0.00412304 | 0.02283930 | 0.06350415 | 0.13467333 |
| Historical mean | 0.00412495 | 0.02315751 | 0.06773395 | 0.15983328 |
| Ridge AR(1) | 0.00411545 | 0.02319535 | 0.06778456 | 0.15988047 |
| Ridge AR(24) | 0.00413608 | 0.02485963 | 0.06944158 | 0.16174203 |
| Volatility empirical | 0.00412445 | 0.02295514 | 0.06611477 | 0.15243266 |

The descriptive point-baseline selector chose AR(1) at 1h and zero at 1d/7d/28d, using this development fold only. Every contrast and the aggregate decision deny a skill claim. The result is **retain-baseline**, with no promotion, final-test or profitability inference. Scores concern returns; price-level fit is not evidence of advantage.

Paired per-asset/per-horizon scores, exclusions and daily loss sums were independently recomputed during acceptance. Comparison used 2,000 synchronized chronological moving-block bootstrap draws, seed 42, the registered 73-day blocks, all 20 declared contrasts, Holm adjustment and simultaneous family-tail bounds. Two separate CLI comparison processes produced identical bytes: SHA256 `ca58300a1e5bd96d318b793353809168e0d1887bd222809ce6d4162cd98caf46`. Probabilistic results include pinball, directional Brier/log scores, coverage, widths and bootstrap intervals. Calibration remains unqualified at every horizon; for example, the nominal 80% ETH 28-day interval covered 74.20% of outcomes. Raw source, daily loss series, fitted parameters and full accepted results remain private lab evidence.

Worker CPU usage ranged from 1.65 to 2.87 seconds, measured worker elapsed time from 1.41 to 2.63 seconds and peak RSS from 92.6 to 93.8 MiB. All runs stayed within the fixed 90 CPU-second, 180 wall-second and 4 GiB memory limits.

## Implementation and qualification

- Shared typed durable request/record/runner primitives preserve forecast JSON codecs and the runtime `JobAttempt`/`JobRecord` classes. Research uses separate protected state files and immutable results, with the existing lifecycle, cancellation, restart/retry fencing and PI01 accounting.
- The application owns the fixed baseline worker branch, bounded input/result schemas and CPU profile. Controls are verified before fitting and before acceptance. The coordinator recomputes predictions and scores from frozen returned parameters without fitting a model.
- Service/CLI parity includes separate-process status and cancellation while a job owns its exclusive store lock. A protected bounded Unix socket forwards only registered status/cancel operations to that owner; cancellation joins worker cleanup. Tests cover long paths, malformed control requests, unsafe socket leaves, no-trial status/probe operations and stale responses after restart.
- The final Debian `scripts/check.sh` passed with Python 3.12.14, uv 0.13.4 and umask 022: both lock checks, locked sync, Ruff/format, mypy (355 files), **2,081 passed / 32 explicitly host-only skipped**, installed base/collector/analysis/TUI wheel workflows, package checks and all seven dependency audit closures. Skipped F21/F22/F24 opt-ins are not native qualifications.
- Separate native F21 qualification on the final source passed all **15 tests**, including missing effective controls, hostile worker outputs, cancellation cleanup and abrupt coordinator death. The distinct PI03 non-fitting probe verified the actual baseline profile. Seven additional real PI03 negative probes on the preceding source identity rejected missing PrivateNetwork, PrivatePIDs, MemoryMax, MemorySwapMax, TasksMax, CPUQuota and LimitAS, without consuming fitting slots. No F22 TimesFM runtime or GPU requalification is claimed.
- Semgrep 1.179.0 and 151 pinned Python rules covered all 28 changed Python paths across the post-review and final constant-only scans: zero findings/errors. Trivy 0.75.0 checked the allowlisted snapshot, both locks, secrets and configuration at all severities: zero findings after the lock corrections. Trivy excludes development dependencies by default; the strict seven-profile pip-audit gate covers those closures too.
- The initial audit detected newly published filelock advisories in existing locks and a setuptools advisory in the forecast closure. Only affected transitive pins were updated: filelock 4.2.1 in both locks and setuptools 84.1.0 in the forecast lock, followed by the embedded reviewed-lock SHA. The [maintainer filelock advisory](https://github.com/tox-dev/filelock/security/advisories/GHSA-v4x8-3wr5-3wq8) identifies 4.2.1 as patched. Failed audit and pin-mismatch runs were preserved; neither is reported as a pass.

Full SonarQube 26.9 analysis completed on the private Debian `Scryntic-PI03` project on 2026-10-24, analysis ID `2b16d24f-c645-4037-b3bb-72cc73556519`: coverage 80.9%, duplication 0.0%. Community Build lacks branch analysis, so this is a separately named snapshot project, not a GitHub PR build. The Sonar way gate returned OK **with no evaluated conditions on its first analysis**; this is not a clean-issue verdict. The server has 109 open labels: 85 code smells, 12 bugs and 12 vulnerabilities. No issue dispositions or gate/profile settings were changed.

The four new vulnerability labels concern seeded bootstrap RNG, used for statistical reproducibility rather than security tokens. Cancellation labels in the launcher were checked against explicit re-raise/join paths and native cancellation/death tests. The immediate `min(..., key=lambda ...)` selector does not retain its closure into a later iteration. The generic type-parameter/field naming label does not represent a Python runtime collision; mypy and runtime-contract tests pass. Complexity, redundant-exception and test-style suggestions remain deferred maintenance. Eight other vulnerability labels are in existing HTTP/isolation-probe code; the analysis does not validate them as new PI03 vulnerabilities.

## Reproduction and evidence identity

Qualified source snapshot: `pi03-20261024T112311Z-7e8188cf9873`, based on `b5537e212f05b0165f98aed15ddace35e80802d3`, 551 allowlisted files. Source archive SHA256 `436b4a3c1a427065a635c597abcf953e3957c4d8161c50f4f77e9d926310fa4c`; manifest SHA256 `7e8188cf9873b1dc285b5abcd29377cff0531be9ab47e3306a4fd8ff94281c0e`; application/Python workload SHA256 `ded2d17842cf1e5ee2648f968fe29485596c6172b1712101fdfc2778ef2368c3`. This validation document was written after those runs.

On an authorized Linux system-manager host, use the same Python/uv pins and protected installation throughout:

```sh
python scripts/pi03_baselines.py prepare --state-dir "$PI03_STATE" --archive-root "$PI03_ARCHIVES"
python scripts/pi03_baselines.py probe --state-dir "$PI03_STATE"
python scripts/pi03_baselines.py run --state-dir "$PI03_STATE" --algorithm zero
# Repeat run only for the other four registered algorithm names.
python scripts/pi03_baselines.py status --state-dir "$PI03_STATE" --algorithm zero
python scripts/pi03_baselines.py cancel --state-dir "$PI03_STATE" --algorithm zero
python scripts/pi03_baselines.py compare --state-dir "$PI03_STATE"
```

Preparation requires the exact admitted retained archives and never opens final labels. `probe` cannot be accepted as a fitted result or consume a fitting slot. `run` is idempotent for a completed identity; interrupted attempts retain their ledger history. A trusted operator must preserve the installation and failed attempts, and must not treat the examples as authorization for extra experiments. Missing system-manager access or controls fail closed. PI04 remains outside this task.
