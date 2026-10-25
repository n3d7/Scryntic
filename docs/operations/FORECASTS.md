# Forward forecast ledger

PI04/KER-64 adds local forecast evidence over the existing application services.
Use an installation with the `analysis` capability and its normal private state
directory. CLI and the terminal UI's **Forecasts** group call the same services.
No exchange credentials or financial execution authority are required or granted.

```sh
scryntic forecasts status --limit 50 --offset 0
scryntic forecasts inspect --slot-sha256 <slot-sha256>
scryntic forecasts compare --left <stream-id> --right <stream-id> --view revised
scryntic forecasts run --wait-seconds 3600
scryntic forecasts mature --limit 4
```

`run` performs one finite origin, waiting up to the supplied bound for the next
hour when the current deadline has passed. Repeated operator invocations can
schedule subsequent origins. It publishes BTCUSDT/ETHUSDT 1h forecasts each hour,
plus 1d/7d/28d at UTC midnight. It uses only the frozen zero log-return baseline
and the exact [approved shadow source/window](../architecture/PI04_LEDGER_CONTRACT.md).
It does not train or consume the PI03 fitting budget. A failed source/clock is
reported; missed historical origins cannot be regenerated as forward forecasts.
Cancellation waits for owned work to settle.

Status is paged and includes deadline, expected maturity, evidence class,
timeliness, lifecycle state and exclusion reason. A zero prediction is valid;
missing, missed, abstained, late, unclassifiable, pending and sealed are distinct.
Inspect a published slot to see its original content/receipt and first/revised
score vintages. Inspect/compare accept `--as-of-ns` for a historical knowledge view.
Reading status or scores does not collect labels or trigger inference.

`mature` performs bounded outcome collection for at most 16 publications and 64
hourly rows per call. Durable cursors advance across calls, including long paths.
Expected maturity (endpoint+60s) does not imply actual outcome availability.
Missing/immature paths are not scored. Later observed corrections append new
outcome/score versions while preserving the original forecast reference close.
Final-crossing paths remain sealed and are not fetched. These commands must run
with the admitted clock/source; unhealthy or uncertain timing cannot certify
genuine-forward evidence.

Comparison reports common pairs and denominators/exclusions by horizon and native
instrument, using matching outcome vintages. A short, unpaired or synthetic cohort
is not evidence of skill, calibration or promotion. The current live adapter has
one frozen stream; comparison of additional declared streams is available through
the service/ledger contract, without admitting another production model.

Keep the installation state and raw objects private. The ledger uses a single
owner, immutable events and bounded capacity; a concurrent owner or integrity
failure is rejected. Preserve evidence on capacity/integrity errors instead of
deleting state or recreating historical receipts. Publication certificates assume
a trusted local owner; they are not an independent public timestamp service.
