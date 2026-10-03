# F19 deterministic replay and baseline evaluation

`ReplayService` evaluates active pinned F18 candle snapshots offline. It consumes
bounded primitives from `DatasetService.inspect`; every native Parquet read stays
in the existing F16 Linux restricted decoder. No live source, credential,
provider/model, arbitrary SQL or financial execution interface is introduced.

```python
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import ReplayService

replay = ReplayService(
    datasets,  # the existing DatasetService over accepted imports
    code_revision=code_revision,
    dependency_lock_sha256=lock_sha256,
)
report = replay.evaluate(
    reference,
    ReplayConfig(
        start_ns=start,
        validation_start_ns=validation_start,
        test_start_ns=test_start,
        end_ns=end,
        mode="as-observed",
        decision_delay_ns=delay,
        decision_uncertainty_ns=uncertainty,
    ),
)
report.canonical_bytes  # immutable canonical JSON; destination handling is F24
report.sha256
```

Inputs require F18 recipe 1.2, dataset schema 1.1 and positive
`label_horizon_steps` (1–128); optional `lag_steps` adds a close lag. Supply actual
code revision and dependency-lock digest for build and evaluation. Old schemas,
invalid lineage, retired pins, oversized inputs and corruption fail explicitly.
Replay does not migrate artifacts or change F08/F18 compatibility.

## Time and leakage rules

`ReplayClock` advances monotonically at caller-supplied nanoseconds without physical
clock reads or sleep. Decisions occur at candle end plus nonnegative delay;
uncertainty defines a synthetic decision interval. `ReplayReader` exposes only
features at the supplied clock; `CandleReplayReader` implements the candle policy.

Historical reconstruction makes finalized values available at candle end.
As-observed additionally requires latest plausible collector receipt <= earliest
plausible decision. F12 budgets from the dataset apply to observations; unhealthy,
stale or out-of-budget evidence fails strict timing. Synthetic decision uncertainty
must fit the recorded budget. This models a schedule, not measured physical time
or workstation possession; offsets do not establish trusted global chronology.

Replay evaluates the fixed F18-selected snapshot. A late backfill/selected revision
is excluded at earlier strict decisions; an earlier revision is not reconstructed.
Build appropriate F18 snapshots to compare revision policies. Features are close
and optional lag_close. Origin and lag candles must be finalized, and every lag
dependency must be available at the decision. Open lags are explicitly excluded
as `lag-not-finalized`; F18's optional open-candle recipe semantics remain intact.
F18 source fingerprints, same-series contiguous chains, values and label finality
are checked before use. Missing derivations remain null with explicit reasons.
Labels are separate reader results; predictions are generated before labels are
requested at the scoring cutoff.

Absolute half-open ranges are train `[start, validation_start)`, validation
`[validation_start, test_start)`, test `[test_start, end)`. The complete decision
interval determines membership; crossing uncertainty is excluded. Labels must end
after the latest plausible decision. A label touching/crossing a partition's right
boundary is purged, as is a label not observable before that boundary. This purges
overlapping multi-step labels on both split boundaries and the test right boundary.
Scoring uses `boundary - 1`; unscorable labels never influence fitting or metrics.

Population standardization fits each feature on eligible training cases only,
separately per `(venue, category, symbol, interval_ns)`. The report records training
row indices, mean and scale; constant columns use scale 1. Validation/test never
refit. No eligible training cases means explicit exclusion for that series. A
wholly empty/unqualified run reports `no-training-data`, counts and empty metrics.
There is no imputation or random split.

## Baseline, reporting and retained evidence

The statistical naive baseline forecasts the last available close for the configured
future close horizon. It has no fitted prediction parameters or randomness.
Standardized features remain available for reproducible preparation/comparison;
the baseline uses exact unscaled close, avoiding inverse-transform rounding. F22
can later consume the recorded contract; F19 adds no provider interface.

MAE, RMSE and signed prediction-minus-actual bias use original price units per
series and split. Different series are never pooled. Training metrics are
descriptive; validation/test are held-out. No profitability, intrabar execution or
forecasting advantage follows. All arithmetic uses a fresh Decimal context: 80
significant digits, half-even rounding, explicit exponent limits/traps. Values are
canonical strings; ambient decimal settings cannot change a run.

Fixed inputs/configuration, code revision, CPython runtime and dependency lock
reproduce logical results and canonical report bytes. No run timestamp or elapsed
time enters identity. Versioned JSON binds exact F18 manifest/Parquet hashes, full
provenance, unchanged pins, configuration, evaluation environment, fit state,
predictions, actuals, timing, metrics and exclusions. Original F18 exclusions and
complete derived lineage remain explicit. Replay never changes/retires pins.
Retain report bytes and all pinned artifacts; metadata alone cannot rebuild deleted
source inputs. Output destination handling and CLI belong to F24.

Coverage/time-quality default to exclude. Historical include retains explicit
flags; as-observed always requires bounded observation timing even with include.
Missing ledger claims stay unknown. Any unresolved overlapping pending/unknown/
unrecoverable claim overrides complete. As-observed coverage capture must be
observable at decision/scoring cutoff; a later repair cannot qualify an earlier
feature. Policies apply to every feature and label dependency. Gaps, missing/nonfinal
labels, purge and quality failures appear as row/partition exclusion reasons.

## Limits and references

At most 10,000 rows and 64 MiB canonical report are accepted. F18 inspection uses
128-row pages; each revalidates the artifact in a restricted worker. This favors
the current bounded contract over large-snapshot throughput; maximum-size replay
has not been benchmarked. Isolation/hashes do not prove physical clock truth or a
compromised worker's numerical honesty. No parser authority or worker capability
is added; native-worker coverage is not injected into the restricted profile.

Context7 `/python/cpython` and Python 3.12 primary
[Decimal documentation](https://docs.python.org/3.12/library/decimal.html) informed
the arithmetic context. The authors' *Forecasting: Principles and Practice* documents
the [naive method](https://otexts.com/fpp3/simple-methods.html) and
[held-out metrics](https://otexts.com/fpp3/accuracy.html). SOFA's
[seeded-shuffle discussion](https://agents.stackoverflow.com/questions/1f985ee1-3e38-4ff0-beea-0781417f114f)
was a limited second opinion on unstable random membership; its hash split is not
used for temporal data. A focused temporal-leakage search returned no applicable
SOFA result. Architecture, F18 source and leakage fixtures govern purging/cutoffs.

See [F19_VALIDATION.md](F19_VALIDATION.md) for validation evidence.
