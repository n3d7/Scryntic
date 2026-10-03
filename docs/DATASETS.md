# F18 datasets and provenance

`DatasetService` is the workstation application boundary for accepted imports:
immutable Parquet + canonical JSON build, bounded primitive inspection, and exact
manifest/Parquet byte export. All native parsing and Arrow computation remain in
the F16 Linux restricted profile after acceptance, including generated datasets.
Hashes, catalog acceptance and pins confer identity, never parser authority.

## Application API

```python
from scryntic.application.dto import BuildDatasetRequest
from scryntic.dataset.recipes import RecipePolicy
from scryntic.dataset.service import DatasetService, F18_RECIPE_SCHEMA

service = DatasetService(
    catalog,
    installation,
    code_revision=code_revision,
    dependency_lock_sha256=lock_sha256,
)
reference = service.build(
    BuildDatasetRequest(accepted_hashes, F18_RECIPE_SCHEMA),
    policy=RecipePolicy(finalized_only=True, lag_steps=1, label_horizon_steps=1),
    coverage=captured_coverage_claims,
).dataset
rows = service.inspect(reference, offset=0, limit=128)
manifest_bytes, parquet_bytes = service.export(reference)
```

Use an open ImportCatalog (including F17's anchored catalog), exact accepted
publication hashes, actual code revision and SHA-256 of the dependency lock used
for the build. Inspection returns fixed columns, exact decimal strings and
primitive values. Export revalidates the complete file inside the worker before
returning the original opaque bytes. No arbitrary query/path, live source read,
downloaded transform, executable deserialization or model invocation is admitted.
CLI/export destinations belong to F24.

## Selection and recipes

Defaults preserve F08: latest non-conflicting revision, open candles allowed,
unknown coverage/time retained with explicit flags. `revision="first"` selects
the first accepted revision; `latest` follows the existing open-revision/finalization
state machine. Both retain evidence. Duplicates produce no extra logical row.
Known rejected/conflicting input and invalid/missing revision history fail even
when a timing filter would otherwise hide them; no new F06 correction semantics
are invented.

Strict `mode="as-observed"` requires an explicit `as_observed_cutoff` ClockSample.
The latest plausible collector receipt must be no later than the earliest
plausible cutoff. Both require acceptable F12 evidence. Healthy status or drift
within budget alone does not qualify overlapping uncertainty. Missing/stale/excess
uncertainty, later revisions and later backfills are excluded. A subset retaining
only a duplicate/revision without its original input fails closed. Historical
reconstruction never claims strict timing.

This is F12 collector-observation chronology, not a claim that the workstation
possessed the data then. First local import receipt is retained separately and
not rewritten on repeated pulls. Time evidence remains a declared host/source
trust claim; hashes do not establish physical chronology.

CoverageClaim is a trusted application capture of F15 ledger evidence for an
instrument/interval/range/status/detection ClockSample. Full capture and canonical
hash are retained and pinned. Publication v1.0 does not transport the recovery
ledger: absent claims remain `unknown`; received rows never imply completeness.
Resolve obsolete ledger entries before capture: any overlapping pending/unknown/
unrecoverable claim overrides complete. Later/ambiguous coverage evidence cannot
retroactively repair an as-observed snapshot. `coverage="include"` flags such rows,
`exclude` removes them, and `require-complete` fails. Historical `time_quality`
similarly accepts include/exclude/require-healthy; strict timing always requires
bounded evidence. Exclusions have reasons. Valid policy filters may yield no rows.

OHLCV remains exact decimal128(38,18). Recipe 1.2 optionally adds lag_close and
finalized future label_close/label_end_ns, bounded to 0–128 steps. Derivations
require matching instrument/interval and every intermediate candle start. Labels
also require finalized future candles. Gaps/missing/non-final values become null
with reasons and source fingerprints, including partial chains; no imputation,
float conversion or fitting occurs. Disabled derived fields are null and omitted
from active definitions. Labels are separate from features. Splits/train-only
fitting/replay evaluation use [F19 ReplayService](REPLAY.md) over pinned snapshots.

## Compatibility

| Reference | Supported behavior |
| --- | --- |
| Candle recipe 1.0 | Original historical selection, dataset schema 1.0 |
| Candle recipe 1.1 | Original strict F12 cutoff, dataset schema 1.0 |
| Candle recipe 1.2 | Explicit policies/derivations, dataset schema 1.1 |
| Dataset Parquet 1.0 | All original fields/types/identities preserved |
| Dataset Parquet 1.1 | Adds nullable lag/label fields, retains all original fields |
| Dataset JSON 1.0 | Original F08 references readable through restricted inspection |
| Dataset JSON 1.1 | Adds import receipts, policy/coverage and derived lineage |

The existing SchemaRef negotiation requires exact name, same major and no newer
minor than supported. Unknown names/incompatible majors/newer minors fail
explicitly. No historical artifact is rewritten or automatically migrated.
Import schema/codec allowlists remain F16's responsibility. Collector-owned F08
builder/reader stays compatible; it cannot decode imported F18 manifests, including
ones using dataset schema 1.0. Workstation callers use DatasetService.

## Immutable provenance, pins and limits

Manifests bind exact publication documents/hashes, object descriptors/schemas/
codec versions, selected inputs and evidence fingerprints, receipt/normalization
time, normalizer versions, ordering/time range, exclusions, feature/label recipes,
coverage capture/hash, code revision and dependency-lock digest. The coordinator
independently compares result identity, exact accepted input descriptors/body,
policy/environment/cutoff/import receipts, coverage hash and output size/digest.
Rebuilding exact inputs/configuration/environment reproduces logical values and
provenance. Writer configuration is recorded; cross-version byte identity is not
promised.

Artifacts use no-replace hash names, flushed files and synchronized directory
entries. Successful build includes a durable immutable retention pin for dataset
manifest, output Parquet and every publication/input object. Interrupted builds
may leave unacknowledged orphan artifacts, never deletion authority.
`service.pins(reference)` verifies active roots. `service.retire(reference)`
explicitly removes only that pin; no source/output deletion occurs. F29 maintenance
must honor all active pins. F18 performs no automatic pruning. Deleted required
inputs prevent rebuilding even if the output snapshot remains readable.

Builds accept at most eight manifests, the configured import record ceiling
(default 10,000), and 128 MiB aggregate encoded input. Output limits are 32 MiB
Parquet, 24 MiB provenance and 64 MiB IPC; inspection returns at most 128 rows.
Existing import encoded/decoded/memory/CPU/wall bounds remain. Arrow reads use
128-row batches with threads disabled inside the worker. No credential/config/
network/model/device access is added; missing controls fail closed. These are
enforced controls, not proof against kernel compromise or malicious numerical
results from a compromised worker.

## External cross-checks and validation

Context7 did not resolve direct PyArrow documentation. API decisions were checked
against Apache Arrow 25.0.1 primary
[ParquetFile](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html),
[write_table](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html)
and [Schema](https://arrow.apache.org/docs/python/generated/pyarrow.Schema.html)
docs. Parquet's compliant list child is named `element`; comparison normalizes
that spelling while retaining metadata checks and disabling extension decoding.
SOFA's [schema-drift discussion](https://agents.stackoverflow.com/questions/86a50d17-bc9a-40de-a522-2225d65984c3)
was a limited cross-check of accepted shapes/auditable failures; its Excel alias
and coercion strategy was not adopted. Project contracts/source/tests remain
authoritative. See [F18_VALIDATION.md](F18_VALIDATION.md) for validation evidence.
