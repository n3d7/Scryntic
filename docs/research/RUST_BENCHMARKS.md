# Python, native engines and Rust placement

Status: proposed engineering decisions, 2026-10-18. No new performance benchmark was executed in this assignment. Existing [F10 measurements](../performance/f10-operating-envelope.md) support retaining the current raw codec on its measured workload; they do not compare Python with Rust or qualify a larger analytical workload.

## Alternatives

| Architecture | Benefits | Costs / decision |
|---|---|---|
| Current Python plus existing PyArrow | Lowest migration risk; best immediate ML integration; existing durability and recovery semantics | Keep as control-plane and first forecasting implementation. Remove measured Python overhead by batching before considering a language migration. |
| Python plus native analytical libraries | Arrow/Parquet interoperability, vectorized queries/transforms, column projection and bounded batches | Selected incremental direction. Keep PyArrow writer compatibility; qualify DuckDB for approved batch queries and compare Polars only where feature transforms benefit. Do not require two new engines for the same job. Native libraries still need OS isolation around hostile input. |
| Python plus selected Rust components | Potential benefit for CPU-heavy deterministic decoding, book reconstruction, replay or feature kernels; typed ownership and concurrency | Conditional option after profiling and an end-to-end benchmark. Process separation is preferred where fault/authority isolation matters. No Rust component is justified for immediate migration by the evidence collected here. |
| Larger Rust core plus Python ML | Consistent native event model; plausible later high-rate event processing | Reject as present roadmap dependency: duplicates working services and increases FFI, deployment, compatibility and recovery qualification costs. Reopen only if several measured bottlenecks cannot be addressed with batching/native engines and a bounded Rust kernel. |

Nautilus demonstrates a viable Rust/Python system, not a measurement that Scryntic needs a rewrite. Its LGPL obligations and trading-oriented runtime are also integration costs. [Reference source inspection](REFERENCE_SYSTEMS.md) and [version/API evidence](ENGINEERING_SECURITY.md) qualify these observations.

## Placement by responsibility

| Responsibility | Recommended placement | Reason / trigger for change |
|---|---|---|
| Application services, CLI/TUI, coordinator, registry and policy | Python | Existing contracts, lower integration cost and strong ML ecosystem. Rust cannot repair a flawed evaluation policy. |
| SQLite journal and immutable publication/recovery | Retain existing Python orchestration/native SQLite/Arrow | fsync/transactions/storage may dominate. Preserve commit/ack/no-replace semantics; changing a codec or language requires fault-matrix equivalence. |
| Networking / adapter supervision | Python asyncio initially | Hourly/daily workloads do not justify low-latency rewrites. Measure queue age, normalization CPU, loss/coverage under bursts before changing networking. |
| JSON/native decoding and normalization | Restricted worker, existing native libraries first | Potential Rust candidate if allocation/parse cost dominates under representative payloads. Rust is not a replacement for quotas, malformed-input handling or trust separation. |
| Order-book reconstruction | Deferred family-specific component; Rust optional | Sequence validity, resets and snapshot/delta recovery must be correct first. Good future kernel candidate if measured sustained workload exceeds the Python/native implementation. |
| Replay / feature scans | Columnar bounded batches first | Current F19 caps 10,000 rows and repeatedly validates 128-row pages. Profile redundant scan/validation and list copying before language migration. Authenticate an immutable object once per isolated scan job and stream batches under the same integrity boundary; do not globally disable validation. |
| Model fitting, tuning and inference | Python ML runtimes | Native frameworks already execute major numeric kernels; keep training separate from coordinator and ingestion. A Rust wrapper does not improve predictive evidence. |
| Serialization / transfer | Existing canonical manifests plus Arrow/Parquet, fixed protocol | Prefer explicit IPC for isolated workers. Arrow transport is not permission to trust unbounded buffers, arbitrary schemas or embedded extension code. |

## PyO3 versus process IPC

Use a separately invoked, operator-approved executable with a bounded versioned protocol when crash isolation, killability, memory/process quotas or hostile inputs matter. Inputs are exact artifact hashes/read-only descriptors; outputs return through restricted validation. IPC adds serialization/startup costs and needs measured batching.

PyO3 is suitable for an admitted pure kernel inside an already restricted worker if batched calls and Arrow/NumPy interchange measurably outperform IPC and existing libraries. Define ownership/lifetime rules, panic/error conversion, GIL behavior for the pinned release, integer/decimal overflow and cancellation. A native extension shares its process authority and failure domain. Package wheels for each supported CPython/platform/CPU target; qualify x86_64 and aarch64 separately when offered. No generic “zero-copy” claim without validating representation and lifetimes.

Do not cross the Python/Rust boundary once per tick/row by default. Compare one batch, one artifact and one complete job. Existing canonical identities must not depend on platform float formatting, hash-map order, thread scheduling or compiler optimization flags.

## Representative benchmark plan

These sizes are proposed test profiles, not supported product capacities. Choose feasible sizes for each enrolled host and publish exact results before changing production placement.

| Profile | Workload | Measurements and correctness evidence |
|---|---|---|
| B1 long-horizon research | 20 assets × 3 years of hourly observations; 1 h / 1 d / 7 d / 28 d targets; 32 and 256 features; gaps, late arrivals and revisions | End-to-end snapshot→PIT features→fold generation→baseline fit/evaluate; wall/CPU time, peak RSS, disk/spill, bytes scanned and parity. |
| B2 analytical scale | 1/10/100 million normalized rows when storage permits; projected columns, as-of joins, rolling windows, multi-asset common cutoffs | Compare current batched PyArrow, DuckDB, Polars and only then a bounded Rust kernel. Include spill limits, cancellation and corrupt/malformed objects. |
| B3 ingestion bursts | Captured immutable public candles and synthetic trades/books with declared provenance; sustained/burst rates swept to failure | Queue age, p50/p95/p99 durable-ack latency, records/s, CPU/RSS, dropped/rejected/gap counts, restart recovery and disk pressure. No synthetic workload is relabeled as venue traffic. |
| B4 book/replay kernel | Venue-specific snapshots/deltas with duplicates, discontinuities, resets, stale messages and invalid levels | Identical validity epochs/output hashes, recovery actions and bounded memory; compare reconstruction/event throughput. Deferred until the family is scheduled. |
| B5 language boundary | In-process Python/native/PyO3 and subprocess IPC with 128/4,096/65,536-row batches | Include startup, serialization, transfer, copies, verification, cancellation and output import; distinguish warm/cold behavior. |

Each run records input/config/source/lock/compiler hashes, seeds, CPU/GPU/driver, OS/kernel/filesystem, storage, cgroups, thread counts, power mode and cache state. Include warm-up and at least five measured repetitions for affordable profiles, medians and tail/spread; publish failures. Use production filesystem semantics and both a declared CPU baseline and GPU hosts only where relevant. Never drop OS caches or alter host settings without the operator's explicit scope. Performance tests must also pass existing durability, schema, timing and hostile-input fixtures.

## Migration criteria

First demonstrate an actual service-objective failure within the intended workload and attribute it through profiling. Fix algorithm, repeated I/O, batching and native-engine use before a rewrite. Proposed Rust acceptance policy: at least 2× improvement in the limiting end-to-end stage or at least 40% lower peak memory at equal latency, plus a material improvement to the actual service objective. These are proposed decision thresholds, not measured results or universal constants.

All accepted migrations also require semantic parity (including exclusions and numeric tolerances), compatible archive readers, fail-closed limits, deterministic supported-profile behavior, crash/recovery evidence, maintainable packaging, license review and a reversible rollout. Reject isolated microbenchmark wins that do not improve the complete job. Keep Python fallback while shadow outputs are compared. Remove it only after a separate reviewed deprecation decision.
