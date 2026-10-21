# PI01 protective foundation

Implemented under the approved PI01 steps 1–3 scope. This is an
additive compatibility and research-safety foundation, not a training runtime.
PI00 policy v2 admits only scoped Binance historical reconstruction; Bybit and
forward source admission remain pending. No PI02 target generation,
fitting, final evaluator, promotion or trading capability is implemented here.

## Registered policy and evidence

[PI00_RESEARCH_POLICY_v1.json](PI00_RESEARCH_POLICY_v1.json) is the exact registered
scientific-rules artifact; its [SHA-256](PI00_RESEARCH_POLICY_v1.sha256) binds the
bytes. Keep it unchanged. A revision must be a new retained artifact, registered
before observing the evaluation it governs.

[Policy v2](PI00_RESEARCH_POLICY_v2.json) retains v1 scientific rules and future
reservation, and adds the verified Binance historical-reconstruction scope. Its
[SHA-256](PI00_RESEARCH_POLICY_v2.sha256) binds the enrollment example below.

| Choice | Registered rule; not an empirical result |
| --- | --- |
| Universe | Bybit linear and Binance Vision USD-M BTCUSDT/ETHUSDT retain separate venue identities. Binance historical reconstruction is admitted; Bybit and forward availability remain pending. |
| Timing | Hourly finalized last-trade candles; nominal return horizons 1h/1d/7d/28d. Reference close at grid time t, issuance/cutoff t+60s; endpoint t+h must still be future. Skip late/missing observations. |
| Historical calendars | Development 2020–2024, calibration 2025, exploratory evaluation 2026-01-01 through 2026-10-01 exclusive. These are requested boundaries, not verified coverage or an untouched test. |
| Final reservation | Future-only [2026-11-01, 2030-01-01) UTC, with mature labels and audited access. No historical independence is asserted. |
| Primary criterion | Log-return MAE skill versus the strongest development-selected registered baseline; meaningful margin 2%, with simultaneous confidence control. |
| Minimum evidence | 240/120/60/40 non-overlapping blocks for 1h/1d/7d/28d, with minimum block lengths 1d/1d/7d/28d. Floors do not establish power or independence. |
| Baselines / selection | Zero return, past-only mean and fixed ridge AR recipes; shared eight-attempt budget, Holm family control and no adaptive holdout peeking. |
| Probabilistic outputs | Score/calibrate only genuinely supported distributions. Unsupported probabilities stay absent. |
| Compute | One CPU job, one numerical thread, 4 GiB RAM, 8 GiB address space, 90 CPU seconds/180 wall seconds; eight worker tasks and 8 GiB retained pilot allocation. No new GPU/remote runtime. |

Forty 28-day blocks require at least 1120 days of suitable forward evidence;
gaps or dependence can require longer. Earlier monitoring is preliminary. The
eight-attempt ceiling and per-job limits may restrict later feasible experiments;
revise policy before evaluation if feasibility cannot be established. Synthetic
tests do not demonstrate predictive improvement, sample power or real-model
runtime qualification.

## Contract surface

`scryntic.research.contracts` is the executable schema authority. Canonical
envelopes are bounded to 64 KiB, use `scryntic.pi.<kind>` version **2.0**, reject
unknown fields/versions and preserve required provenance bindings.

| Kind | Current contract role |
| --- | --- |
| `target` | Explicit log-return units, ordered duration horizons and price basis; no label computation. |
| `dataset-feature` | Original DatasetRef hash/schema/row count, feature-recipe reference and honest unknown availability. |
| `forecast-distribution` | Truthful v1 price-point adapter; expected/median return, calibrated direction probability and calibration hash are null, quantiles empty. A new probabilistic provider needs separately qualified semantics. |
| `experiment` | Hypothesis-family and registered-policy binding with bounded trial budget. |
| `task` | Experiment/environment hashes, seed and CPU/memory ceilings; no executable or arbitrary command field. |
| `evidence` | Synthetic, historical-reconstruction or forward class; forward registration timestamp is mandatory for a forward declaration, not proof of actual pre-outcome performance. |

`adapt_dataset` and `adapt_forecast` produce new wrapper identities and preserve
the referenced v1 identity. They do not rewrite archives, datasets, pins, provider
requests/results or durable-job envelopes. Unknown historical knowledge times
remain null. Contract validation does not itself grant execution or scientifically
qualify a worker's declarations.

## Enroll a protected research installation

Enrollment is trusted operator wiring, not a client-supplied approval or automatic
production migration. Use an explicitly selected **separate private research
Installation**, before any fitting. Do not enroll an active collector/production
installation or a journal already containing unrelated jobs: all its jobs would
become subject to the shared pilot budget. No live installation was enrolled by
this task. The examples/tests use synthetic private installations.

```python
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from scryntic.research.ledger import ResearchLedger, Reservation

# installation: an operator-selected, existing private research Installation.
raw = Path("docs/architecture/PI00_RESEARCH_POLICY_v2.json").read_bytes()
policy = json.loads(raw)

def utc_ns(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()) * 10**9

reservation = Reservation(
    policy_sha256=sha256(raw).hexdigest(),
    final_start_ns=utc_ns(policy["final_holdout"]["start"]),
    final_end_ns=utc_ns(policy["final_holdout"]["end"]),
    development_end_ns=utc_ns(policy["chronology"]["development"][1]),
)
with ResearchLedger(installation) as ledger:
    ledger.reserve(reservation)
```

Verify the sidecar digest and selected installation before executing enrollment.
Registration requires a future final start relative to the actual clock; never
override the clock to enroll an already observed period. If the date has passed,
register a new genuinely future policy first. Re-enrolling the same reservation
is idempotent; replacing it in place is rejected. Enrollment does not activate
sources, implement OS label separation or authorize fitting.

## Application enforcement and accounting

Presence of either research journal or head anchor enables protection; a missing
counterpart, corrupt content or empty/unregistered reservation fails closed.
Application APIs expose no delete/reset/final-read grant. DatasetService checks
the whole snapshot before restricted decoding for inspect/export, including
pagination. Label-bearing, unknown or out-of-development ranges are denied.
New raw builds are blocked in protected installations: partition dates alone do
not prove safe event/label bounds. Precise protected input admission belongs to
PI02. Existing accepted development snapshots retain their identities and pins.

JobService checks the verified input/forecast time span, including custom input
readers, and F18Inputs also traverses guarded DatasetService inspection. Jobs
still use the original durable scheduler/store and model admission policy.
Registration, attempt start, terminal state and operator intervention are audited.
JobStore reconciles committed states idempotently after transactions and on
startup, including expiry triggered by reads. If audit writing fails after a
SQLite commit, the state may already be committed: inspect/reconcile on recovery,
never report an unaudited operation as successful. Provider cancellation cleanup
still runs if audit or state inspection raises.

These are application controls under a trusted operator. Model workers must
receive only approved descriptors/artifacts, never host paths or the research
state directory. A host administrator can rewrite/remove both audit files; hashes
do not attest an honest administrator. Losing both files can remove opt-in
protection, so preserve their backup/installation binding and do not use a
workspace lacking its expected reservation. No unrestricted research execution
or OS-level sealed final-label store is delivered by this milestone.

## Journal durability and rollback

Fixed private `research-ledger.jsonl` and `research-ledger.anchor` files live in
the existing Installation.state_dir, with owner-only regular-file checks, an
exclusive flock, inode checks and fsync. This is a bounded audit artifact, not a
second SQLite writer or scheduler. Limits: 8 MiB, 10,000 events and eight pilot
configurations/started attempts globally. Events are canonical JSON with a
sequence and previous-event digest; the anchor binds the committed prefix.

The journal is synced before advancing the separately synced head. A complete,
valid unanchored tail can recover forward. Partial/corrupt tails, unknown schemas,
missing anchors and truncation below the committed head fail closed. There is
no automatic truncate, quota pruning, evidence erasure or anchor reset. After
failure preserve both files and use a verified consistent backup/recovery
procedure. An anchor and journal should be backed up together while quiescent;
do not copy a live SQLite database as a substitute for its supported backup.

The provider-job SQLite schema stays version1; artifact schemas/identities stay
unchanged. Without enrollment, Foundation v1 behavior is retained. Old software
can still read the original artifacts, but does not enforce the new reservation:
**do not roll back protected research execution to that software** or delete the
journal to make rollback appear to work. Stop research and retain its evidence;
use a protection-compatible release or separately controlled legacy-only reader.

See [migration](MIGRATION.md), [fixture pins](../validation/PI01_COMPATIBILITY_FIXTURES.json)
and [validation](../validation/PI01_VALIDATION.md) and execution evidence. A successful synthetic run does
not complete the unresolved source-data/scientific admission gates.
