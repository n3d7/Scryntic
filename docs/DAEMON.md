# Foreground daemon (F23)

Start the collector with the locked collector environment:

```sh
uv run --locked --no-sync python -m scryntic.daemon
```

The minimal bootstrap uses the existing workstation XDG installation policy.
Configuration, state, runtime and credential directories must already satisfy
the existing ownership, no-follow and private-mode requirements. Startup never
provisions directories, credentials, remote enrollment or privileges. The
default profile collects public Bybit spot BTCUSDT one-minute candles. Category
and symbol are explicit bootstrap settings. `--producer` and `--epoch` select
stable journal identities; retain them across restarts. The default epoch is
`daemon-v1`. `--profile fixture` selects deterministic bounded test input.

Full CLI workflows, socket/report APIs, Telegram, systemd deployment units and
hot reload remain outside F23 (F24–F27).

## Composition and ownership

`Services` opens the existing raw ingestion journal, normalization/publication
stores, coverage supervisor and durable job journal. Startup validates storage
reserve and recovers existing publication and job checkpoints before intake.
The durable pipeline owns one store executor, with the raw journal retaining
its existing writer. Clock evidence and optional synchronization have separate
executors. Job dispatch uses a continuously running owner loop and retains
execution ownership independently of cancelled callers, with a bounded dispatch
limit. Job state summaries contain counts rather than requests or result bodies.

ClockMonitor remains on the collector event loop. Synchronization evidence is
queried off the loop and aged under F12; each capture samples actual host clocks.
Missing/degraded evidence permits safe capture but cannot authorize timing or
finality claims. A clock capture failure stops collection explicitly. Source
configuration faults disable the source until corrected; existing F15 transport
retry and coverage recovery isolate transient failures. Storage exhaustion,
normalization barriers and publication failures stop new intake and retain the
last durable checkpoints.

Queued/interrupted jobs are recovered without automatic execution. A trusted
composition caller may supply an existing JobService through `job_factory`.
No daemon setting changes provider admission or F21/F22 worker isolation.
Optional `sync_factory` supplies a pre-enrolled, authenticated F17 PullBinding
on its synchronization owner thread. The default collector has synchronization
disabled. Remote pull failures degrade synchronization; local catalog/storage
failures fail closed. F24 owns operational enrollment and job workflows.

## Shutdown and restart

SIGINT/SIGTERM close collection and job admission before drain starts. Accepted
writer/executor work remains owned until it actually completes. Drain settles
jobs, joins source operations, and advances eligible normalization/publication
work. Resources close only after actual owners settle. One shared deadline
covers drain and close. Repeated signals/cancellation request a forced stop.

A deadline overrun or failed durable drain produces failed/incomplete health
and a nonzero exit. A foreground watchdog exits this process when its bounded
shutdown budget expires, including a stalled executor or event loop. It never
reports a cancelled awaiter as a terminated worker. WAL/FULL transactions,
active coverage sessions and publication recovery govern the next startup.
An uncommitted record may disappear; an acknowledged durable record must remain.
Restart and socket reconnection do not establish continuity. F15 unknown tails
remain visible even when the bounded span ledger cannot append another span.

Job-thread completion includes cleanup: failures from the job store, async
generators or executor shutdown remain observable after joining the thread.
Independent resource cleanup still runs, and a cleanup failure produces a
nonzero process exit. Final failure events get at most 50 ms to flush before
forced exit; a blocked sink cannot extend that wait indefinitely.

Forced coordinator death cannot run Python cleanup. External workers retain
their existing F21/F22 isolation and independent runtime limits; an operator
must inspect residual unit/staging state under that procedure. F23 does not
claim a whole process-tree cleanup after SIGKILL or requalify model isolation.

## Health and logs

The explicit states are starting, healthy, degraded, stopping and failed.
Readiness means initialized services can accept safe collection; degraded
readiness does not prove completeness or timing authority. Liveness describes
the cached daemon heartbeat separately. Shutdown clears readiness immediately.

`Services.health()` and `Daemon.health()` read bounded immutable cached state
without SQLite, network, time-service or filesystem I/O. There are at most 64
stream summaries; JSON is limited to 64 KiB. Snapshots expose transport and
data freshness separately, coverage gaps/unknown tails, clock evidence and
epoch, queue/backpressure, storage reserve, normalization/publication faults,
sync status, durable job counts and shutdown phase/pending owners. A fresh
transport heartbeat cannot hide stale market data. Unavailable clock evidence
is represented by null fields.

`received` counts receipts in the current process, including receipts rejected
before durable admission; it is not a durable offset or continuity proof.
`durably_committed`, `normalized` and `published` are recovered producer-journal
offsets. They may exceed the current-process receipt count after restart.
Unfinished execution ownership and durable work left behind are distinct:
checkpoint differences retain durable backlog even after owners have joined.

Closed JSON event/field schemas go to stderr, suitable for journald capture.
Payloads, arbitrary identifiers, exception text and library/task representations
never enter those events. The bounded logging queue isolates intake from a slow
sink; overflow increments the logger's drop counter. Logging cannot guarantee
delivery after forced death or sink failure. F23 adds no journal service unit.

Actual verification and limitations are recorded in [F23_VALIDATION.md](F23_VALIDATION.md).
