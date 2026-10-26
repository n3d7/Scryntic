# PI05 bounded scan qualification

## Decision

**Retain paginated PyArrow in the production replay path.** The one-pass PyArrow scan is faster on these synthetic jobs but exceeds the registered +10% peak-memory allowance on both 4,096-row workloads. DuckDB exceeds that allowance on every workload. Do not relax the memory threshold or promote an engine after viewing results.

DatasetService.scan is an additive, closed, integrity-preserving primitive capped at10,000 rows. It remains separate from ReplayService, which retains its existing128-row paginated inspection path, including restricted validation for empty snapshots. No engine/SQL selector or production DuckDB dependency is introduced. DuckDB2.0.0 is pinned only in the explicit benchmark dependency group and included in dependency auditing.

## Registered comparison

[Original protocol](protocol-v1.json) was frozen before execution. Three deterministic development fixtures contain512,4096 and4096 gapped hourly rows; they include exact ns identities, Decimal strings, nulls/non-finalized/quality cases and horizon masks at1/24/168/672 hours. No market download, fit, final holdout, PI03 trial, forecast maturation, score append or financial execution was performed.

Each candidate has one warmup and seven measured fresh process jobs per workload. Candidate order is interleaved with seed6505. The OS cache is not cleared; validation hashing is included and can warm inputs. Results are descriptive evidence from one host, not a capacity or predictive claim. The paired bootstrap uses10,000 resamples, seed6505 and a95% interval over the seven process ratios; its upper bound must be at most0.75, with peak cgroup memory at most1.10 times the baseline, on **every** workload.

The pages comparator retains repeated input reads, hashes, descriptor sealing and full validation per128-row page. All three paths run through the common qualification launcher; this measures the registered scan/verification job, not the production replay service or complete forecast scoring latency. Fixture preparation, controller polling and system-manager setup are outside measured service start-to-exit time. Interpreter/worker startup, IPC, complete row consumption, oracle/mask verification, serialization and report fsync are inside it.

## Administrative recovery

The operator stopped the original campaign after59 completed records, during physical attempt60. Subsequent host reboots prevented completing that workload within its original boot epoch. [Amendment01](protocol-amendment-01.json) was recorded before recovery launches: retain48 records from the two complete workloads, preserve/exclude11 completed records and one operator-cancelled intent from the partial third workload, then repeat **only** that workload's24 registered jobs on one new boot. Aggregate physical cap84 includes the cancelled attempt; the comparison contains72 records. No candidate failure was discarded, and no configuration, semantic contract or adoption threshold changed.

The standalone [recovery controller](../../../scripts/pi05_recover.py) imports the original frozen controller/measurement helper. It verifies checkpoint, protocol, fixture, source, runtime and lock identities, writes/fsyncs intents before launches, retains failures, and fails closed on boot/source/runtime drift. An exclusive persistent root-owned claim keyed to the original checkpoint prevents another recovery using a different evidence directory. The original execution source was not modified. Recovery follows installation of the process logger; candidate pairs never cross boot epochs. This is an explicitly disclosed post-start administrative amendment, not an uninterrupted original run.

## Results

| Workload | Path | Median job wall (s) | Peak cgroup memory (MiB) | Peak memory change | Adoption gate |
| --- | --- | ---: | ---: | ---: | --- |
| hourly-4096 | duckdb-2.0.0 | 1.339 | 101.23 | +35.20% | no |
| hourly-4096 | pyarrow-pages | 13.731 | 74.87 | +0.00% | no |
| hourly-4096 | pyarrow-scan | 1.212 | 83.73 | +11.83% | no |
| hourly-4096-gaps | duckdb-2.0.0 | 1.330 | 101.17 | +34.86% | no |
| hourly-4096-gaps | pyarrow-pages | 13.686 | 75.02 | +0.00% | no |
| hourly-4096-gaps | pyarrow-scan | 1.208 | 83.72 | +11.60% | no |
| hourly-512 | duckdb-2.0.0 | 0.816 | 58.50 | +25.02% | no |
| hourly-512 | pyarrow-pages | 1.816 | 46.79 | +0.00% | no |
| hourly-512 | pyarrow-scan | 0.748 | 47.96 | +2.50% | pass |

All completed comparison runs passed exact row/order/ns/Decimal and per-horizon eligible/excluded/common-input parity. Each backend passed its effective isolation checks. The one-pass scan's median wall reductions are approximately58.2%,91.2% and91.2%, but its4,096-row memory increases are11.83% and11.60%. Therefore the original memory gate blocks adoption despite the speed gain. DuckDB adds approximately25.0–35.2% peak memory and provides no necessary product capability for this workload. Polars and Rust were not introduced.

[Sanitized72-record evidence and decision](results-v1.json) includes whole-job wall/CPU, coordinator and worker RSS, cgroup peak/I/O, encoded and output bytes, scan/decode counts, zero-spill policy and cleanup. Raw evidence, source transfer receipt, manager/PID/boot identifiers and the twelve administrative exclusions remain private. Public identifiers are source/runtime/lock/fixture/protocol hashes and non-identifying environment epoch labels.

## Integrity and resource boundaries

Every request still validates the complete immutable object, including hashes, schema, lineage/count and malformed rows outside any selected page. Full scans have an independently validated primitive response and the existing research-ledger guard. The inspection API remains capped at128 rows; full scans are capped at10,000. No result or trust cache survives the request. Native DuckDB is loaded only after the existing isolated bootstrap and full Arrow integrity oracle; the additional native scan is counted.

Workers retain no ambient network, host directories, secrets or registry/financial authority; read-only sealed inputs, empty capabilities, no-new-privileges, zero new threads, address-space/CPU/wall/output bounds and process-group reaping remain. The bootstrap actively probes identity, environment, network, process creation, file writes, descriptors and allocation beyond RLIMIT_AS. DuckDB uses one query thread, disables extension autoload/install/unsigned/community extensions, allows only the admitted input, locks external-access configuration and actively rejects re-enabling it, INSTALL and an unapproved path. No profile relaxation was used.

Outer jobs are fresh cgroups:3GiB MemoryMax, no swap, TasksMax16, one-core CPU quota and180s start/runtime bound. Worker limits remain2GiB address space,10 CPU seconds and20 wall seconds. The zero writable-spill policy is enforced by filesystem/seccomp denial; spill_bytes=0 expresses that policy, not a separate spill telemetry counter. Cgroup output I/O includes the report write and is not spill. Decoded-byte accounting covers actual Arrow batches; additional DuckDB decoded allocations are not separately observable, while the whole-process/cgroup peak still includes them. RSS and cgroup memory are reported separately, never substituted for one another.

## Prerequisite disposition and rollback

No PI01–PI04 identity, revision, availability, score, policy or scientific gate required a change. The prerequisite gap was repeated bounded scan work; the additive primitive and isolated comparison close the measurement gap, but the gate does not admit routing replay to it. Prior completed issues remain unchanged. Reverting the additive scan/qualification files restores the base implementation; the production replay route is already unchanged. No schema migration, data rewrite, model artifact or release/promotion action is involved.

## Verification and reproduction

Focused tests cover full scans, empty inputs, oversize/malformed primitives, exact fixture drift and masks, controller/cleanup failure retention, immutable fixture/source/runtime binding, incomplete decisions and the single-use recovery budget. The existing host-control tests cover deadline/output/stderr flood and descendants holding IPC pipes. Final scripts/check.sh passed2157 tests with32 unrelated explicit host skips, type/style/lock checks, four installed-profile/package checks and all eight audit profiles including benchmark. Debian scoped scan/replay tests passed69 tests, and final effective-control/deadline/flood/descendant checks passed9 tests in3.24s. The task/PR report retains the failed stale audit expectation and its correction; host-specific F21/F22 tests outside PI05 are not claimed from ordinary local skips.

For an isolated exploratory body-only run, install the locked explicit benchmark group, prepare a fresh synthetic fixture with scripts/pi05_scans.py prepare, and run scripts/pi05_scans.py run with one of the three closed candidate names. Such a run does **not** supply outer cgroup metrics or qualify adoption. The operator campaign requires a verified allowlisted execution archive/manifest/transfer receipt, pinned runtime, registered protocol and a fresh evidence directory; do not edit existing measured source or reuse recovery admission. The exact source/archive/runtime/lock identities used here are in results-v1.json. Publication bookkeeping, the recovery controller and the retained replay route are outside that frozen measured worker artifact.

Host stability remains unresolved: the lab has spontaneous unclean reboots, including one after the completed recovery. A process/reboot logger preserves evidence; no warning/preceding process is asserted to be the cause. These measurements do not qualify hardware stability or establish results for unregistered sizes, concurrency, arbitrary SQL, aggregates/joins, spill, models or production capacity. Future optimization needs a separately registered task rather than silently spending more attempts here.
