# PI03 pre-fit contract

Registered before any PI03 fit on 2026-10-23. Authority: explicitly approved KER-63, frozen PI00 policy v2 and PI02 feasibility spec v1. The separate [PI03 specification](PI03_BASELINE_SPEC_v1.json) admits only its fixed development workload; it does not modify either predecessor's no-fitting statement. This is private, noncommercial historical reconstruction, with unknown original first-seen/revision times.

One fixed forward development fold trains from 2020-01-02 until the fit cutoff 2021-08-01; every training outcome must mature strictly before that cutoff. Evaluation origins span `[2021-09-01, 2025-01-01)` UTC, identically for BTC and ETH. Their 24-hour information intervals start on or after 2021-08-31, more than 28 days after fitting. Endpoint plus 60 seconds must precede the evaluation end. No later refit, policy calibration partition, exploratory partition or final labels are used.

| Horizon | Complete evaluation origins per asset | Nonoverlapping blocks at the registered minimum duration | Required floor |
| --- | ---: | ---: | ---: |
| 1h | 29,231 | 1,218 (1d) | 240 |
| 1d | 1,217 | 1,217 (1d) | 120 |
| 7d | 1,211 | 173 (7d) | 60 |
| 28d | 1,190 | 43 (28d) | 40 |

These are **pre-fit structural upper bounds**, calculated from the frozen hourly grid and UTC boundaries, not a proof of statistical independence or power. Actual gaps, exclusions and a larger training-estimated dependence span can reduce effective counts. The implementation must report the actual per-interval counts before fitting and deny a claim below any registered floor; adding assets never multiplies independent time blocks. There are no other PI03 evaluation intervals. A multi-fold split with 40 independent 28-day blocks in each fold is infeasible in the five-year development range; this registration therefore uses one large fixed evaluation interval rather than weaken the floor.

Actual retained-source preparation before any fit is recorded in [PI03 pre-fit counts](PI03_PREFIT_COUNTS_v1.json). The admitted development input SHA256 is `bf30c207a6e952cc3a033b053ec4ac651f69cd42a743e3172dc8e79e99c1aff3`. The registered training-only dependence diagnostic yields **73 days**, giving **17 dependence-adjusted nonoverlapping time blocks for every horizon** in the single evaluation interval. All claim floors therefore fail before fitting; the bounded runs remain descriptive development baseline comparisons, and no skill or promotion claim is allowed. This does not weaken a threshold or expand the final period.

Five fixed configurations consume PI01 trial slots, including failures: zero return, training-only historical mean, ridge AR(1), ridge AR(24), and a volatility-scaled empirical return distribution. Ridge alpha is 1.0 and preprocessing uses training rows only. The probabilistic family, quantile definition, directional probability and scores are specified before outcomes are viewed; no Gaussian residual adapter is introduced. No empirical probability is assumed calibrated.

Results use identical pairs per horizon, explicit denominators/exclusions, return MAE/RMSE and supported probabilistic scores. Inference uses 2,000 paired chronological moving-block bootstrap draws with seed 42, synchronized across assets and algorithms. The training-estimated dependence span may only lengthen blocks. Holm controls the declared family; simultaneous lower bounds must exceed the original 2% margin. Every comparison remains provisional historical development evidence. Null or insufficient evidence retains the baseline. No final, live or profitability claim is admitted.

Fit/evaluate runs must use the existing qualified CPU isolation and durable job engine, with fixed typed admission, immutable bounded inputs/results, PI01 accounting before fit, fencing, cancellation/recovery, no network or secrets and service/CLI parity. Missing effective controls fail closed. Tests use synthetic fixtures and separate ledgers; actual experiment attempts cannot be reclaimed by resetting an installation. The reserved final interval `[2026-11-01, 2030-01-01)` stays sealed. PI04 is outside this contract.
