# Documentation

Start with [current implementation](research/CURRENT_STATE.md) for the delivered Foundation baseline and the repository-root [target architecture](../ARCHITECTURE.md) for future direction. Plans and research do not establish delivery; validation applies to its recorded date, revision and environment.

| Directory | What it contains |
| --- | --- |
| [architecture](architecture/readme.md) | Product boundaries, decisions, roadmap, migration, threat model, pilot gates and preserved architectural history. |
| [data](data/readme.md) | Collection, clocks, durable ingestion, normalization, coverage/recovery, imports, datasets and replay; nested publication, transfer and performance guides. |
| [operations](operations/readme.md) | Configuration/runtime guidance, daemon and CLI/TUI usage, durable jobs, model provisioning/workers and operator transport handoff. |
| [validation](validation/readme.md) | Quality gates, feature qualification and dated SonarQube review records with JSON/CSV companions. |
| [plans](plans/readme.md) | Feature implementation plans and earlier design specifications. The active product sequence is in the architecture roadmap. |
| [research](research/readme.md) | Academic and source-level evidence, model/framework comparisons, engineering/security research, data-use checkpoints and Consensus records. |

Each directory's `readme.md` explains its files and links to deeper sections. Two workflow documents remain at this level because agent instructions refer to their stable paths:

- [WORKFLOW.md](WORKFLOW.md): task approval, implementation and delivery conventions.
- local `HANDOFF.md`: current local continuation checkpoint, updated after substantial tasks and before an unfinished task is handed over. It is ignored by Git and may be absent in another clone.

Local `task-reports/` and `local-audits/` have their own indexes when present, but are ignored by Git and may be absent in another clone. Authored plans, shared skills, workflow guidance and runtime guidance are eligible for Git; personal agent instructions, filled handoffs, private reports and generated measurements remain excluded. See [local backups](operations/LOCAL_BACKUPS.md) for preservation and recovery. Preserved historical architecture and private task/audit records retain their original text and recorded paths.
