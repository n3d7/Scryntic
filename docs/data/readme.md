# Data pipeline and contracts

Read these for the implemented Foundation data path: collection, durable acceptance, normalization, immutable publication, imports and offline use. Feature numbers identify their original delivery scope.

| File or directory | Purpose |
| --- | --- |
| [BYBIT.md](BYBIT.md) | Public Bybit historical/live collection, supported adapter scope and read-only account boundary. |
| [CLOCK.md](CLOCK.md) | Clock-quality monitoring, chrony evidence and temporal admission assumptions. |
| [INGESTION.md](INGESTION.md) | Durable raw-record intake, SQLite writer ownership, acceptance and local crash recovery. |
| [NORMALIZATION.md](NORMALIZATION.md) | Deterministic normalization, event types, processing checkpoints and recovery behavior. |
| [RECOVERY.md](RECOVERY.md) | Coverage states, continuity evidence, source supervision and conservative repair policy. |
| [IMPORTS.md](IMPORTS.md) | Hostile manifest/Parquet admission, opaque storage, restricted native decoding and trust limits. |
| [DATASETS.md](DATASETS.md) | Pinned candle datasets, provenance, quality checks and restricted inspection/export. |
| [ML_PREPARATION.md](ML_PREPARATION.md) | Opt-in feature preparation, configurable source-use decisions and reproducible evidence bundles. |
| [REPLAY.md](REPLAY.md) | Deterministic offline replay and baseline evaluation over admitted dataset snapshots. |
| [performance/](performance/) | Measured codec/storage operating envelope; see [index](performance/readme.md). |
| [publication/](publication/) | Immutable archive publication and recovery; see [index](publication/readme.md). |
| [sync/](sync/) | Authenticated resumable workstation pull and its evidence; see [index](sync/readme.md). |

[Up to the parent index](../readme.md).
