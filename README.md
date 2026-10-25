# Scryntic

Work in progress

The proposed [Predictive Intelligence architecture](ARCHITECTURE.md) prioritizes
validated hourly, daily, weekly and longer-horizon return forecasts. See the
[research record](docs/research/WORKING_RECORD.md),
[implementation reconciliation](docs/research/CURRENT_STATE.md) and
[staged roadmap](docs/architecture/ROADMAP.md). The redesign awaits architecture
approval; its capabilities are not yet implemented.

F10 capacity measurements, raw codec selection and configurable retention sizing:
[operating envelope report](docs/data/performance/f10-operating-envelope.md).

Workstation F17 enrollment, authenticated resumable SFTP pulls and trust-anchor
recovery: [operator/API guide](docs/data/sync/F17_PULL.md).

F18 reproducible imported datasets, temporal/coverage recipes, schema compatibility
and restricted inspection/export: [dataset guide](docs/data/DATASETS.md).

F19 deterministic offline candle replay, temporal splits, training-only transforms
and statistical forecast evaluation: [replay guide](docs/data/REPLAY.md).

F20 explicit provider/model admission, durable jobs and bounded loopback HTTP
fixtures: [provider jobs guide](docs/operations/JOBS.md).

PI04 immutable forecast evidence, finite frozen-baseline scheduling and outcome
vintages: [forward ledger guide](docs/operations/FORECASTS.md).

## License

Scryntic is licensed under the [Apache License, Version 2.0](LICENSE).
See [NOTICE](NOTICE) for project attribution.
