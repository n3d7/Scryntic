# Compatibility and migration plan

Status: proposal, no product implementation performed. Baseline: `main` `8e1465fd389e440763ef69b7c59832ad2f8bd8de`, F01–F24 Done; [current-state reconciliation](../research/CURRENT_STATE.md). Migration is incremental and reversible; [roadmap](ROADMAP.md) owns sequencing.

## Preserve and extend

| Existing area | Preserve | Target extension / required proof |
|---|---|---|
| Ingestion, normalization and publication | Durable journal ack, producer/epoch identities, offsets, raw bytes, canonical schemas/codec, no-replace publication and recovery conflict semantics | New data families use conformance tests and family-specific continuity/revision rules. Stress/recovery parity before enabling them. |
| Clock and coverage | Clock-quality uncertainty, strict versus reconstructed timing and complete/pending/unknown/unrecoverable evidence | Add explicit availability planes and knowledge intervals without upgrading old unknowns to trusted times. |
| Import/SFTP | Restricted native parsing, pinned endpoints, hashes/lengths, no-follow checks, quotas, accepted anchors and rollback protection | Add artifact task transfer through separate agent credentials; remote training is not generic SSH execution. |
| `dataset.service.DatasetService` / F18 | Exact accepted-input manifests, immutable snapshot identity, schema compatibility, lineage/pins and restricted inspection/export | New PIT/feature manifests reference old objects and new metadata. Dual-reader fixtures, no in-place conversion. |
| `replay.service.ReplayService` / F19 | Caller-driven replay, chronological cutoffs, feature/label separation, purge and train-only fitted state | Add return targets, common asset origins, probabilistic scores and larger bounded batch readers. Keep old price reports readable and honestly classified. |
| Provider contracts / TimesFM | Explicit capability/license/model identities, immutable artifacts and forecast-only authority | New versioned train/distribution interfaces; v1 providers remain usable in their declared scopes. Requalify actual weights/runtime; no implicit eligibility from model name. |
| `jobs.service.JobService` / F20–F22 | Admission, durable state, attempt/result binding, effective isolation, safe output import, actual-owner cancellation and recovery | Extend task kinds and then leases/fencing. Migrate durable records transactionally with backup/readback and interruption tests. |
| F23 daemon / F24 services | Ownership, bounded intake, health, shared typed workflows, safe errors, installed/headless profiles and optional TUI | New operations extend the registry; interfaces remain clients. No ML framework imports in collection/coordinator. |
| F25–F30 superseded operations plan | Original issue definitions, relationships and all Done history | Under explicit operator follow-up the unfinished issues/project are Canceled. Required reporting/deployment/retention gates move to PI17, extension proof to PI18/PI06, release qualification to PI21; Telegram is optional. This is not implementation completion. |

Paths above are conceptual entry points, not a patch plan. Material current bodies were inspected through graph/snippets and direct source; graph coverage is best effort. The implementation milestone must recheck current source and tracker state before edits.

## Rollout stages

1. **Inventory and approval:** hash/archive v1 fixtures and schema/manifest/lock identities; record current anchors, pins and installed profiles; approve target contracts, data rights, experimental policy and budgets. Reserve a genuinely uninspected final period and establish minimal append-only trial/access accounting before the first baseline fit/ablation. If already observed, use a later untouched period. Old Foundation plan remains historical, not authoritative target behavior.
2. **Additive v2 contracts:** write new manifests/reports alongside v1. Unknown major versions fail closed. A v1→v2 adapter preserves original identity and explicitly marks unavailable fields/evidence class. Never synthesize availability history or calibrated probabilities.
3. **Baseline vertical slice:** select feasible source frequency/history and add bounded deterministic resampling plus duration-based endpoint extraction; existing v1 limits of 1–128 label steps and 10,000 replay rows cannot silently express weekly/monthly targets from minute bars. Add a minimal fixed baseline fit/evaluate task on the current verified CPU launcher and durable acceptance path, then comparisons and the pre-outcome ledger. Current providers are forecast-only; general admitted ML training arrives later. Validate availability/aggregation, deterministic replay, missing-history blockers and service/CLI parity before a broad tournament.
4. **Batch/PIT shadow:** compare existing bounded implementation and candidate batch engine on exact fixtures, including malformed files, late/revised facts, mixed units, gaps and split boundaries. Use field-level expected parity and documented floating tolerances. Keep current readers and rollback path.
5. **Research/qualification separation:** expand the early reserved-final-range and trial/access controls into the full independent registry and immutable releases before broad search. Verify that the final range remained unobserved; otherwise replace it, never relabel it as untouched. Introduce technical admission and scientific state separately. Production remains on approved prior releases while candidates run in shadow.
6. **Remote workers:** local task contract first, then one enrolled CPU host; validate lease expiry/partition, stale completion, cancellation, coordinator restart, artifact corruption and host revocation. GPU requires its own profile and evidence. No fleet-wide rollout from a single successful test.
7. **Autonomy, operations and UI:** add bounded policies, recovery/backup/retention drills and private management only after core contracts are stable. Minute/second and live trading remain later independent work.

## Compatibility gates

- Old archive/dataset/forecast fixtures remain readable and reproduce their original canonical hashes/identities. New outputs have new schema and identity; no claim of byte identity across schema versions.
- New time/target semantics cannot silently reinterpret old step counts, candle finality or timestamps. Tests include incomplete knowledge time, different arrival planes, reset producer epochs and common cross-asset boundaries.
- Decimal/integer storage units survive adapters; model float conversions, overflow/rounding and inverse transforms are explicit. Native engine/Rust parity includes exclusions, not just numeric rows.
- Durable-job/schema upgrades preserve interrupted/cancelled/accepted history. Failure at each migration/publication boundary converges or fails closed; no success from a staged artifact alone.
- Pins protect accepted datasets, model training evidence, forecasts and trust-anchor restoration. Retention/compaction cannot invalidate a registered reproducibility claim unnoticed.
- Collector/analysis/worker/UI permissions and dependencies remain separated across installation profiles. Existing isolated CPU qualification remains revision-scoped; changed controls require requalification.

## Rollback and recovery

Back up operational catalogs using their supported consistent procedure, retain schema/version inventory and immutable objects, and rehearse restore into a separate installation. A rollback cannot reuse a committed producer sequence or reset accepted anchors. If old code cannot read a migrated DB, restore a verified snapshot or use an explicitly new epoch; never force an unsafe downgrade. Registry rollback selects an older admitted immutable model release compatible with current feature/target contracts.

Run old/new readers in shadow before replacing composition defaults. New analytical catalogs/caches are rebuildable; raw accepted evidence is not assumed recoverable from external providers. Data that cannot be licensed/retained or reconstructed remains an explicit release blocker for claims requiring it.

## Risks requiring an implementation decision

Historical availability may be absent for funding/OI/news/on-chain sources; cross-venue mappings and survivorship-free universes may require additional licensed history. Storage growth and native parsing surfaces grow with wider data. Final holdouts can become contaminated by repeated access or foundation pretraining. GPU/remote hosts broaden the trusted computing base. New training workflows may need more than existing 10,000-row replay limits. These risks are addressed by measured, bounded milestones; no untested capacity or security guarantee is inferred from this design.


## Implemented PI01 compatibility boundary — 2026-10-21

The additive [protective foundation](PI01_PROTECTIVE_FOUNDATION.md) wraps v1
identities in strict v2 research envelopes without changing provider-job SQLite
schema1, archive/Parquet bytes, dataset schemas, pins or anchors. Exact unchanged
HEAD [fixture pins](../validation/PI01_COMPATIBILITY_FIXTURES.json) include the
historical F07 Parquet artifact and v1 dataset/replay/job/forecast fixture code.
This is local implementation evidence, not published production migration.

A separate private research installation must explicitly enroll its reservation
before protected work. No production/collector workspace was enrolled by this
task. In enrolled installations raw builds are intentionally blocked until safe
input-range admission exists; label-bearing, unknown and reserved snapshots are
unavailable through inspect/export and job paths. The append-only JSON journal
and durable head share the existing state-directory boundary and do not introduce
another SQLite writer. Preserve both files and follow the guide's crash/recovery
rules; never erase them or reset a committed head to unblock work.

Old software remains an artifact reader, but does not enforce the reservation.
It must not execute protected research after rollback. Stop research, preserve
its audit/history and use a protection-compatible release or a separately
controlled legacy-only reader. No in-place archive conversion or anchor reset is
part of migration. Full PI00 source/coverage admission remains open; no fitting
or PI02 work is authorized by this compatibility foundation.
