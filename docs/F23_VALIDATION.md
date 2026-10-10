# F23 / KER-27 validation

Base: main `aeff60ee76190ab024aaebd22dca1e4b14db21da` (F22 qualification complete).
Validation date: 2026-10-10. All mandatory local checks passed. The two scoped
SonarQube False Positive dispositions received explicit user authorization and
were applied with source/test rationales; the subsequent full analysis passed
the unchanged gate. See [the independent review](sonarqube/f23-review-2026-10-10.md).

## Scope and evidence

The foreground host composes the existing raw/coverage, normalization,
publication, F12 clock and F20 durable-job services, with optional trusted F17
pull composition. No dependency, security policy, worker launcher, isolation
unit, quality threshold or scan exclusion changed.

The previous temporary checkout disappeared between sessions. Source and tests
were recovered from the local SonarQube analyzed snapshot into persistent
`.worktrees/ker27`; syntax/format repairs and source-backed fixes
were validated again. Historical test claims are not substituted for current
validation. The original main checkout and its untracked user reports remain
untouched.

Current executed checks:

| Check | Observed result |
|---|---|
| Pinned uv 0.12.18; `bash scripts/check.sh` | Exit 0: 1871 passed, 31 skipped in 113.60 seconds; lint/format (351 files), strict mypy (289 files), both lock checks, coverage XML, packaging/profile checks and dependency audits passed. |
| `python scripts/check_negative.py` | Exit 0: all seven intentional test/lint/format/type/lock/resource/audit-unavailable violations rejected; inputs unchanged. |
| Foreground checkpoint tests | Both graceful SIGTERM and SIGKILL at eight raw/publication durability boundaries pass; recovery matches actual committed offsets, including rollback before COMMIT and durable commit before acknowledgement. |
| Actual foreground signal/watchdog tests | Graceful SIGTERM, stalled executor, blocked event loop, repeated SIGINT/SIGTERM and sanitized process-control failure pass. |
| Independent focused review | Earlier 16 targeted current-worktree host tests plus 15 final cleanup/foreground/logger tests passed; drain completeness, fatal clock capture, F15 unknown tail, sync storage/remote isolation, active-job ownership and actual thread cleanup reviewed against exact source. |
| Actual public foreground daemon | Exit 0 after self-SIGTERM following 12 seconds of public Bybit collection: received/committed/normalized/published all 5, queue 0, transport connected and fresh, clock unknown and source faults visible as degraded readiness; graceful shutdown reports zero unfinished operations. No finality inferred. |
| Existing `python -m scripts.check_bybit_recovery` | Exit 0; accepted 8, normalized 8, published 1, dataset rows 1, finalized rows 0; clock healthy; source-confirm/F12-final flag false. No finality inferred from that short capture. |
| Unprivileged F12 host probe | Synchronization evidence available, but the later four-sample probe reported unknown with reason `stale_evidence`. No host clock changes, relaxed limits or elevated commands performed. Safe capture does not imply timing authority. |
| Semgrep local unchanged Python security rules | Exit 0, 12 implementation paths, 0 findings, 0 errors. |
| Trivy filesystem vuln/misconfig/secret scan | Exit 0; both uv locks detected; 0 vulnerabilities, misconfigurations or secrets. DB updated 2026-10-10T12:33:35Z. |
| SonarQube full local analysis | Final scanner exit 0 at 20:58:43 UTC; gate OK, 82.4% new coverage, 80.9% overall coverage, 0.0% duplication, 0 open findings/vulnerabilities/hotspots. The preceding exit 3 was caused only by the two independently verified false positives (`S3516`, `S7497`); exact issue keys, evidence and authorized transitions are recorded in the linked review. |

The gate uses real filesystem access. The filesystem sandbox previously caused
spurious no-follow/private-path failures; it is not used as durability evidence.
The public integration used public Bybit only, with no credentials or time edits.

## Failure and recovery coverage

Focused tests cover intake-before-drain, startup validation/recovery, shared
deadline expiry, repeated signals/caller cancellation, stalled executors,
cancellation-resistant jobs and retained dispatch slots; workers remain owned
until actual completion. They also cover source configuration/transient faults,
separate data/transport freshness, bad/missing/stale clock evidence and capture
failure, queue pressure, storage reserve/exhaustion, normalization barriers,
publication faults, failed/expired/interrupted jobs, and local versus remote
synchronization failures.

The eight checkpoint boundaries are before raw COMMIT, after raw COMMIT before
acknowledgement, publication reservation commit, raw object, normalized object,
partition views, prepare commit and catalog commit. Graceful drain publishes
accepted eligible work; forced restart never invents acceptance or skips raw
records. Received process counts remain separate from durable journal offsets.

Closed health/log schemas are checked with sentinel arguments, payloads,
exceptions and library/asyncio errors. Bounded cache/JSON and blocked/dropped log
sink tests verify non-blocking reporting. Newline/control data never becomes a
new log field. Coverage history retains conservative unknown-tail accounting.

## Technical decisions and review

[Stack Overflow for Agents](https://agents.stackoverflow.com/questions/e7a183af-691d-4f74-ab9b-bb0d2af50240)
challenged thread shutdown and caller-detached ownership. Its process-pool
example was not copied: cancellation of the proxy future or early semaphore
release does not prove termination. Current behavior was verified locally.
Context7 and [Python task documentation](https://docs.python.org/3.12/library/asyncio-task.html)
confirm shield ownership and that `wait` reports pending tasks without waiting
for cancellation. [Executor documentation](https://docs.python.org/3.12/library/concurrent.futures.html)
confirms that `shutdown(wait=False)` does not guarantee prompt interpreter exit.

Source review resolved four actual defects: drain failure falsely reported
complete; clock sampling could silently terminate a background task while old
health remained healthy; quota-bounded F15 unknown tails were omitted from the
health count; job-thread cleanup exceptions leaked raw text and falsely allowed
exit 0. Each has a concrete regression test. Cleanup failures now propagate
after joining, all independent cleanup stages are attempted, and a real child
exits 1 with a structured failed-shutdown event and no sentinel/Traceback.
Baseline Sonar had zero open
issues and hotspots; current attributable findings are not presented as
pre-existing defects. No server disposition has been made without separate
operator authorization under `docs/QUALITY.md`.

## Limits

The 31 optional host/model qualification cases remain skipped under their
existing gates. F21/F22 qualification is the prerequisite recorded on main;
F23 does not repeat privileged qualification or claim new model-isolation
evidence. Real F23 foreground/clock/public-source checks ran unprivileged.

The default collector uses the existing public candle recovery bridge. Optional
sync and job providers are trusted composition inputs; enrollment, job workflow
CLI and external report interfaces remain F24/F25. Missing instrument metadata
may leave recovered raw work durable but ineligible for normalization until
metadata is available; checkpoint gaps and a failed drain remain visible.

Forced coordinator death cannot execute cleanup. Existing independent external
worker runtime/isolation controls remain authoritative; residual units/staging
require the existing operator procedure. This is not a 72-hour release soak,
power-loss/filesystem qualification, F27 reboot/deployment qualification, or a
whole process-tree termination guarantee after SIGKILL. Logging may drop events
under a stalled sink and cannot guarantee final delivery after forced death.
