# F24 CLI, application services and TUI implementation plan

> Agentic workers use the task workflow and independent review. No publication
> happens before mandatory verification passes.

**Goal:** Complete installed CLI/application use cases and a standalone optional
Textual terminal UI, preserving F18/F19/F22/F23 contracts.

**Architecture:** Extend the F09 argparse entry point. Both interfaces build
the same fixed typed requests from interface-neutral workflow definitions and
invoke reusable services. Finite collection uses a fixed unprivileged child foreground host and the F23
watchdog; parent cancellation reaps actual processes. Concrete composition remains outside inward
application/domain contracts. Existing subsystems retain authority over
durability, time/coverage, restricted decoding, synchronization and models.

**Tech stack:** Existing Python 3.12, argparse, SQLite and subsystem contracts;
Textual 8.2.8 as an optional TUI dependency. No network/report API or deployment
units are added.

## Constraints and review focus

- All writes use approved protected paths; export uses immutable no-replace
  completion boundaries. No arbitrary command, SQL or remote-path surface.
- Fixed JSON envelope version 1, bounded results and closed diagnostics.
- Status describes available evidence and unavailable live observation honestly.
- Cancellation retains actual worker owners; graceful job interruption differs
  from explicit terminal cancellation. No automatic restart dispatch.
- Durable model-job contexts bind immutable dataset, origin, clock and context
  digest; restarting never selects mutable latest data.
- Headless installations do not import or require Textual; UI background work
  does not infer termination from cancellation of its presentation awaiter.
- Existing user reports remain untouched. New local task reports are Git-ignored
  and contain no secrets; focused user documentation is tracked.

## Tasks

- [x] Define typed workflow requests, bounded results and fixed error/exit codes;
  test request validation and JSON independently of either interface.
- [x] Implement configuration, source discovery, bounded collection/backfill
  and truthful offline status over current F04/F15/F23 contracts.
- [x] Implement accepted-data resolution, dataset build/inspect/exact export
  and deterministic replay with F18/F19 provenance and restricted decoding.
- [x] Implement enrolled read-only sync status/pull and explicit trusted
  enrollment descriptors using F17 credential ownership and generated paths.
- [x] Implement provider inspection, analysis and durable job workflows through
  F20/F22, including immutable restart context and actual-owner shutdown.
- [x] Extend installed argparse CLI and add optional Textual dashboard/navigation,
  searchable workflows/tables, validated forms, progress, shortcuts, confirmations,
  safe cancellation and graceful unavailability.
- [ ] Exercise each workflow through an installed wheel and directly through
  services; test unsafe/malformed inputs, cancellation/restart, missing resources,
  partial export failures, TUI navigation and headless compatibility.
- [ ] Run scripts/check.sh, negative controls, relevant integrations, full
  SonarQube, Semgrep and Trivy. Independently review source-backed findings;
  record actual evidence, limitations and technical challenges from SOFA and
  Context7/upstream docs. Commit/push/PR main; no merge/auto-merge; KER-28 In Review.
