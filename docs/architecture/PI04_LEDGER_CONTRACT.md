# PI04 forecast ledger contract

KER-64, 2026-10-25. This contract preserves PI00/PI02/PI03 scientific thresholds,
the PI03 trial ledger and Foundation v1 security boundaries. It grants no fitting,
calibration, promotion, ModelRelease or financial execution authority.

## Admission and authority

The operator approved the concrete source/window extension after its consequences
were explained, waived external licence qualification during development, and
authorized private Debian retention. The exact immutable admission is
[PI04_FORWARD_SUPPLEMENT_v1.json](PI04_FORWARD_SUPPLEMENT_v1.json), SHA-256
`9b885c4de13b59aa1d38c82165323b69336d3a46fd423aaf26e3d22feb4d04e7`.
The exception is an operator decision, not a third-party data grant; skipped checks
are recorded in the ignored local development licence register.

Only read-only public Binance USD-M `time`, `exchangeInfo` and hourly trade-price
`klines` for native BTCUSDT/ETHUSDT perpetuals are admitted. Shadow origins/inputs
are restricted to `[2026-10-24, 2026-11-01)` UTC. Binance Vision remains a separate
historical source. REST close candidates require server time past the exact close;
first observations and subsequent revisions retain their actual receipt times.
Source content and observation attempts have separate identities. A failed or
unqualified observation may be followed by a fresh, currently timed certificate;
it never changes an existing forecast receipt. Identical qualified observations
reuse their first availability, while A-to-B-to-A corrections retain three
observation vintages. Failed refreshes retain previously available scores.
No exchange credentials, order endpoint or model/framework loader is involved.

Final `[2026-11-01, 2030-01-01)` remains sealed. Long forecasts crossing that range
may be recorded, but their entire outcome path remains unscored and unfetched.
Historical reconstruction, backcast, simulated-as-observed and synthetic fixtures
retain their declared classes. They never acquire forward evidence through replay.
The admitted baseline is frozen deterministic zero log-return, point output only.

## Identity and durable publication

Separate identities bind the logical slot, canonical content, execution attempt
and publication receipt. Slots bind stream ID, native instrument, origin, horizon
and target; changing a model/code manifest cannot create a new slot to bypass a
conflict. A stream admits one frozen manifest. Content binds original reference
close/version, input lineage, availability cutoff, code/runtime and point value or
explicit abstention. Identical retries return the original receipt; different
content for an occupied slot appends an explicit conflict.

The owner commits content and its reference atomically in private single-owner
SQLite with WAL/FULL, pinned files, strict schema and a bounded hash-chained event
journal. A retained fsynced head detects loss of an already anchored suffix.
After publication COMMIT, a trusted UTC/monotonic/quality sample bounds its actual
publication time; a separate durable certificate attests to that transaction.
The certificate timestamp does not assert when its own fsync finished. Worker,
replay and log timestamps never certify durable acceptance.

Acceptance requires a healthy clock interval with upper bound `<= origin+60s`
and strictly before the endpoint. Unknown, degraded, regressed, stale or restarted
clock evidence cannot certify timeliness. Genuine-forward additionally requires
owner admission and matching durable raw input/input certificate observed on time.
A forward declaration alone grants no evidence: late and unclassifiable forward
publications remain distinct. After a crash, an uncertified publication receives
a permanent recovery-unknown receipt; recovery never backdates it. A surviving
certificate keeps its original evidence. Integrity assumes a trusted host owner;
no independent public timestamp or exchange-truth attestation is claimed.

## Scheduling and bounded execution

Existing DurableRunner/DurableJobStore provide attempts, fences, cancellation,
retries and restart. Inference auditing is separate from fitting: no PI03 start
is consumed, reset or reclaimed. The executor performs bounded zero-return
arithmetic and loads no untrusted ML code; it claims no new native worker profile.
BTC/ETH 1h slots occur hourly; 1d/7d/28d slots occur at UTC midnight. Due slots,
missing inputs and missed deadlines are explicit. Recovery cannot fill historical
gaps as forward. CLI runs are finite; cancellation settles the actual owner.

Limits include one active inference, 64-slot batches, 10-second/2-MiB HTTP requests,
10,000 events/8-MiB payload, and 4,096 raw objects/512 MiB. Source storage verifies
private immutable hash-addressed files. Exhaustion fails closed, preserving evidence.
Maturation handles at most 16 publications and 64 fetched rows per invocation,
with durable round-robin and per-path cursors so pending or long paths progress.

## Outcomes, revisions and comparison

Expected maturity is endpoint+60s, separate from actual observed outcome
availability and score-as-of. The original forecast reference close never changes.
Complete, observed hourly paths before final may append parent-linked outcome
vintages and score versions; missing, immature, invalid, unqualified and sealed
paths retain reasons. New scores cannot be backdated or use future knowledge.
Scores bind forecast/outcome/scorer/as-of identities and finite point absolute,
squared, magnitude and direction components; no intervals/probabilities are invented.

First-available and revised-as-of views preserve prior scores. Comparison uses
common native source/instrument/origin/horizon/target and matching truth vintage,
with per-horizon/instrument denominators and exclusions, including unpublished
missed/missing slots. Short or synthetic cohorts imply no skill, calibration,
significance or promotion result.

Typed application workflows expose status, inspection, comparison, finite run and
maturation to both CLI and TUI. Read workflows do not collect labels or mutate
budgets. PI05 is limited to these stable lineage, resource and status contracts;
no analytical engine, optimization or benchmark is implemented here.
