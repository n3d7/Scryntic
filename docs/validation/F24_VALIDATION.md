# F24 / KER-28 validation checkpoint

Status: implemented and qualified within the recorded Debian CPU/operator roles.
The user explicitly authorized the eight reviewed Sonar dispositions; they were
applied and read back, and the final mandatory gate is OK. Publication remains
pending approval. No push, PR, merge or auto-merge has been performed.

Branch: `ker-28-f24-cli-application`, started from F23 merge
`1ecd65e83f0cd9e914c94df3b22d1a1f657e9525`, then fast-forwarded to current main
`918584f0e3145cd14d9d154a4f14a750f22eb3e2`. The intervening changes only update
README, CONTRIBUTING and the pinned setup-uv action; uv remains 0.12.18. All 353
application/script/test hashes remain unchanged. Installed packaging and Trivy
were repeated with the new README and checked-in GitHub configuration present.
Current source/test snapshot SHA-256:
`c9be23c9f0a71e29cd40f7d2179366cff5db2725c7eee7eec31b1d9bd3be7f57`.
This ledger hashes the sorted path/SHA-256 records as compact JSON. The
earlier transferred snapshot was
`2de1f749e464063694712e782962c41a7e38eaa676408f6722cdef58d877d0ca`;
the `04337ef1a5d146163c4bfc36f49ebf10cc359f742a52e1f24a909802d5281754`
overlay changed only the deterministic artifact-test mtime fixture. The current
`f83d07f18328f5872ca78e33a2d1365d515da2fecf67e69aa560e8182ab0e742`
snapshot fixes finite live completion and owner-init cleanup, with eight
deterministic regression cases. The current overlay only constructs a request
before an exception assertion to resolve the genuine Sonar S5778 test finding.
All production/script bytes, measured coverage and locked dependencies remain
identical to the full-gate snapshot; the complete affected test file, Ruff,
formatting and mypy were rerun successfully after this test-only clarification.
The October 12 Fedora results below describe the earlier
`d92c4defa0bfef4097d582b0a01928c19fb499d61c69a937551a1caad7ca5a30`
snapshot and must not be relabeled as current lab results.
The snapshot covers 353 files: tracked/new source, scripts, tests, locked package
metadata and `.gitignore`; documentation is outside that digest. A separate
archive inventory hashes all 381 transferred files, including CPU runtime locks,
build constraints and supporting documents. Both ordinary and operator source
copies were verified against it before running the new checks.

## Implemented behavior

The existing argparse entry point and optional Textual 8.2.8 TUI use the same
23 typed workflows and application services. Core installations require neither
Textual nor analysis/collector extras. JSON has a bounded versioned envelope,
closed diagnostics and documented exit codes. Installed services can be called
without importing the CLI. See [F24 usage](../operations/F24_USAGE.md).

Finite collection/backfill compose F23 in a fixed unprivileged child. Shutdown
retains and reaps the actual owner, including pipe overflow, deadlines and
repeated cancellation. A finalized fixture can complete without pretending it
has a live transport; real source failures and daemon live-health faults remain.
Data/replay retain F18/F19 temporal, coverage and restricted-decoding checks.
Synchronization uses F17 authenticated read-only access and protected enrollment
leaves. Durable jobs bind immutable dataset, replay/clock context, source revision
and dependency lock before dispatch or restart through F20/F22.

## Debian qualification on October 16–17, 2026

The dedicated operator-controlled Debian 13.7 host has kernel
`6.12.111+deb13-amd64`, systemd `257.13-1~deb13u1`, cgroup v2, an i5-8400
(6 CPU cores), 32 GiB RAM and an approximately 111 GiB SSD. The environment uses
root-owned uv 0.12.18 and CPython 3.12.14. No GPU or broader host profile is
qualified. Provisioning and administrative operations were confined to the lab.
Sonar, PostgreSQL and scanner state remain private/loopback-only; no SSH
authorization or firewall changes were made.

| Check | Actual result |
| --- | --- |
| F21/F22 host suite on the earlier recorded snapshot | **31 passed**, 509.08 seconds, zero skips: real model execution, effective isolation, missing controls, resource limits, cancellation and abrupt coordinator death/restart. Isolated model/job subsystem bytes remain unchanged; the later application-owner fix has separate installed verification. |
| F24 strict successful-worker pytest | **1 passed**, 9.63 seconds, using the real launch. |
| Strict installed analysis portion | **Pass, exit 0**: authenticated read-only pull, data/dataset/replay, actual isolated execution and durable job cancellation/recovery. |
| Recorded native wheel `c30a8b4bc88d` strict analysis and model rerun | **Pass, exit 0**: the unchanged strict successful-worker verifier, then fresh CLI and independent-service TimesFM jobs with 24 points each. All 161 installed source files match; coordinator model imports remain absent. The subsequent README-only package metadata change preserves all of those source bytes. |
| Native installed CLI and independent services | **Pass**: 161 installed source files match; each accepted and reopened a 24-point TimesFM artifact on a real 192-row public Bybit dataset. The service did not import the CLI; the coordinator did not import Torch. |
| Actual server shutdown/reboot | Both accepted native artifacts reopened successfully with unchanged hashes after the user's server power cycle. Shutdown method was not observed; this is not an additional simulated SIGKILL test. |
| Post-reboot standalone TUI suite | **12 passed**, 20.09 seconds, including cancellation, navigation, cleanup and sanitized diagnostics. |
| Full `scripts/check.sh` on `f83d07f18328` | **Pass, exit 0: 1971 passed, 32 opt-in host skips**, 559.86 seconds for pytest. Both locks, Ruff, formatting, mypy, deterministic wheel/sdist, all four installed profiles/workflows and dependency audits pass. The 31 separately qualified host cases and strict successful-worker check above are additional, not inferred from these skips. |
| Earlier full gate, attempt 03 | **Failed: 1 failed, 1962 passed, 32 skipped**, 594.75 seconds. It exposed the corrected owner-init race. Attempt 01 failed the corrected timestamp-dependent fixture; attempt 02 was interrupted. These retained attempts remain failed/interrupted. |
| Existing negative controls | **All seven pass, exit 0** on the full-gate snapshot: tests, lint, formatting, typing, lock, resources and unavailable audit; injected inputs remained unchanged. |
| Final test-only Sonar clarification | **19 affected-file tests pass**, 0.92 seconds; Ruff, format and mypy316 pass. No measured production/script or dependency bytes changed, so existing full coverage and installed/security evidence remain applicable to those identical bytes. |
| Public Bybit finite collection/backfill on the current wheel | **Pass, exit 0**: CLI and independent services each recovered four historical candles with complete coverage, and each live session received, committed, normalized and published three records before successful bounded shutdown. No source faults were hidden by changing daemon health. |
| Scoped independent correction review | No findings; all **25 tests passed** in an independent ordinary-user run. Full mypy checks **316 files**; Ruff and formatting pass. |
| Fresh October 17 Semgrep | **163 files, 151 pinned Python rules, one known raw warning, zero errors**. The source-backed assessment below still applies. |
| Fresh October 17 Trivy | **Zero CVE, secret or supported misconfiguration findings**, including dev dependencies: 57 main-lock and 26 CPU-runtime package entries. No supported configuration targets were detected. This is not an OS/package-wide vulnerability assessment. |
| Final lab Sonar against exact F23 after explicit review approval | **OK: zero new violations, 83.8% new coverage, 0.0% duplication, zero Security Hotspots**. Exactly eight individually reviewed False Positive dispositions were newly authorized, applied and read back with their exact English comments. All 52 pre-existing raw F23 issues and every gate condition remain unchanged. The built-in 398-rule Python profile and Sonar-way thresholds were preserved. |
| Current-main package/configuration recheck | **Pass, exit 0**: deterministic wheel/sdist and all four installed profiles/workflows rerun after the README-only metadata update. Trivy with checked-in GitHub configuration present reports zero CVE, secrets or supported misconfigurations; its detected targets remain the two uv locks. |

The native dataset manifest is
`a041754771fde2fd89974ce8f3ba0db6411ec71f7726df67face0bf19f834595`.
The accepted CLI artifact is
`fa69d6321617496558e852006c3d5be08399ee87520604852555255d9cc7a2d2`;
the independent-service artifact is
`47fe4fab8f64cab9fc130c6a9160ca91583606ef172e09f683fbce5f9d47939c`.
The model revision remains `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`,
with a separately locked, explicitly enrolled CPU runtime. No context was padded
or fabricated to meet a nominal maximum window. The artifacts do not establish
forecasting advantage or independent holdout performance.

Fresh native jobs on the current wheel accepted CLI artifact
`3b0ed08844f53076e2744903e6fe3b486df184305190a694ebce8d86a8367065`
and independent-service artifact
`fa172496f88ebd00d68ac8d83747ef4b44eaa037d5fd37b232ec1c873f2fa271`.
Both reopened successfully. The first new strict-analysis harness attempt failed
before exercising the verifier because its scratch parent did not exist; the
second used a fresh existing private scratch directory and passed. Both logs
remain preserved; no production change was needed for that harness correction.

The initial Sonar pass found nine new issues. Its one genuine test-clarity
finding was corrected and read back as CLOSED/FIXED. A second report's green
gate was invalid because the harness changed `projectVersion`, advancing the
previous-version baseline. That report is retained and is not used as acceptance
evidence. The main-branch definition was restored and pinned to the exact F23
analysis `e26b9ee2-3e92-40c3-87ed-054344eacc94`; global defaults, gate thresholds,
profiles and issue states were untouched. The final analysis uses stable
`F24-KER-28` version metadata and confirms the correct F23 period and eight
remaining findings before authorized review. That retained pre-review gate is
ERROR. The user subsequently authorized exactly those eight dispositions;
the final readback gate is OK against the same exact F23 baseline.

Independent source/test review recommends exactly those eight findings as false
positives: four cancellation handlers rethrow after actual ownership settles;
four private-thread handlers deliver failures through ready/done Futures after
cleanup. Sixteen targeted cases and eight additional fatal-cleanup injections
passed independently. Proposed exact issue IDs, individual English comments and
test evidence are retained in the ignored local review report. The user provided
the separate authorization required by `QUALITY.md`; exactly those comments and
False Positive transitions were applied and independently read back. No other
issue or control was changed.

The host qualification's 512-row deterministic model workload reported
7,685,949,625 elapsed ns, 7,893,972,000 CPU ns and 2,139,040 KiB maximum RSS.
These are measurements of that declared workload, not a throughput guarantee or
the resource use of the separate public-data CLI request.

The earlier wheel used for the initial native results is
`7703b7884df8c2e73de7d270260120223a775c4fb89e8da866c263fd3c173ff7`;
the sdist is
`9b06c758b0388f57851b0748aa83324c8b16a3e998b8cf4109f51348d5eb86f1`.
A wheel rebuilt from that sdist matched byte-for-byte. Four ordinary-user
installed profile checks remain part of the full gate, not inferred from this
artifact comparison.

The native qualification wheel before the later README-only main update is
`c30a8b4bc88df8524a753b9be6f3fb2b797edb00c7f29e513e934af0e067bbe2`;
the current sdist is
`de7457c020fde4575bc82716d324030e14c86cce2d8dd2ee766a4ffa00d11c93`.

The final current-main wheel is
`5c7eaa202b06973ca6b1d21f0ce7e5c3d4eca379a8ae1b888c20f9bb7511d129`;
the sdist is
`e93be8f46695894aa4f6a775de520e5c02123b0dbaee4832d5d6a466ce2e32e1`.
The package verifier rebuilt it deterministically and checked all 161 installed
source files for each applicable profile. Native model evidence above applies
to those identical application/runtime sources; it is not relabeled as a native
execution of the later README metadata artifact.

### Execution roles and retained limitations

Collection intentionally rejects UID 0. Ordinary CLI/TUI/profile qualification
runs as UID 1001; actual system-manager/model qualification uses the existing
explicit privileged operator path. The strict installed analysis portion calls
the unchanged `verify_analysis(..., require_worker_success=True)` from outside
the checkout. It does not run the collector prelude as root. A broad root
`check_package.py` attempt failed at the expected collector ownership boundary;
its aggregate status is **failed**, despite its earlier successful F24 pytest.
A combined installed `analysis-host` invocation has not been claimed successful.
No application command automatically elevates privileges.

Worker staging is qualified with operator `umask 022`. An initial private test
harness's `077` caused a real systemd `200/CHDIR`; the failure and fixed-label
manager diagnosis are retained. Only the harness execution conditions changed;
worker isolation and permissions policy were not weakened. This pre-existing
umask limitation remains. Another harness assertion initially treated canonical
JSON decimal strings as numeric objects; the successful CLI artifact was retained
and checked in its documented format, then independent service execution passed.

Automatic approval review rejected a persistent model-unit polkit grant as a
separate authorization-boundary change and identified existing explicit sudo as
the safer alternative. The grant was not installed. Tests continued through that
existing operator path, without changing authorization.

### Corrections exposed by the lab

The pre-existing configuration reader compared opened-file metadata but did not
revalidate its current pathname after reading. The minimal correction performs a
no-follow stat relative to the pinned parent, applies existing file policy and
compares device/inode. Three deterministic regressions substitute a symlink,
regular file or missing leaf while preserving the original descriptor metadata.
The original negative control remains. Independent review found no concrete
regression; the local full suite passed 1963 tests with 32 host skips. This
point-in-time check is not a claim that every transient or later replacement can
be detected.

A separate existing artifact test assumed a same-size rewrite always changes
mtime/ctime. Debian reproduced equal timestamps in 99/100 trials, while every
accepted snapshot still contained exactly the approved bytes. Seeding an old
mtime caused 100/100 intended metadata-change rejections. The fixture correction
keeps the production copy/hash checks and original rejection assertions intact;
all three artifact tests pass. Timestamp comparison remains best-effort mutation
evidence, not an unconditional concurrent-mutation detector.

Attempt 03 also exposed a F24 job-owner shutdown race: initialization can fail,
close its event loop and still be settling its terminal future while the caller
starts cleanup. Scheduling `loop.stop` on that already closed loop raised
`RuntimeError` before the real thread join. The correction skips scheduling after
failed readiness, retains the actual join through cancellation and observes
cleanup failures in the caller. Both deterministic ordering/cancellation variants
pass; all 19 job-operation tests preserve existing cleanup exception behavior.
Python 3.12's [event-loop documentation](https://docs.python.org/3.12/library/asyncio-eventloop.html#asyncio.loop.call_soon_threadsafe)
explicitly documents the closed-loop `RuntimeError` during another thread's
shutdown. SOFA discussion `e7a183af-691d-4f74-ab9b-bb0d2af50240`
challenged the cancellation/actual-execution distinction. Its later reply
`70c7f9a1-6692-49cd-8f46-e3b5827e254a` identifies an unsafe timeout example;
that example was not adopted. Source ordering, deterministic tests and an
independent 25-case run establish the local correction.

A separate observation-only live-source probe recorded healthy receipt and
durable publication of seven records before the intake barrier, then a derived
source fault only after intentional transport closure. Daemon health correctly
reports the closed transport; the finite operation must retain the actual
pre-stop source outcome and genuine late faults when deciding completion.
The correction retains source evidence at the intake barrier and genuine late
faults/coverage evidence for terminal completion. All six healthy, stale,
disconnected and early/late source-failure cases pass. Final health still reports
the disconnected transport and SOURCE fault. Historical repair counts and
live-session receipt counts are distinct: a backfill may commit four repaired
candles while its live `received` counter stays zero.

Private manifests, source preimages, failed/interrupted attempts, unit records,
model enrollment and public-data capture are retained. Each new pytest attempt
uses a unique basetemp. Evidence collection excludes generated SSH private keys
and whole test configuration/credential trees. Interrupted portable attempt 02
ended with a Textual teardown error following Ctrl-C; its dead pane and missing
terminal status are recorded, not interpreted as passing. The separate normal
TUI rerun passed after reboot.

## Actual checks on October 12, 2026

Checks ran as ordinary UID 1000 with real filesystem ownership and IPC. Sandbox
metadata and IPC restrictions are unsuitable for these ownership tests. Pinned
uv 0.12.18 was used; no sudo, policy changes or privilege elevation were used.

| Check | Observed result |
| --- | --- |
| `scripts/check.sh` portable gate | **Passes, exit 0**: 1960 passed, 32 host tests skipped; pytest 178.47 seconds. Locks, Ruff, format, mypy (315 files), installed packaging and dependency audit pass. |
| Mandatory real-host positive | Still **fails** when selected: `test_analysis_console_uses_real_pull_data_replay_and_model_worker` / installed `analysis-host`: real `jobs run` returns incomplete exit 7 instead of required success 0. |
| Existing negative controls | All seven pass; deliberately broken tests/lint/format/types/lock/resources/audit inputs remain unchanged. |
| Installed package profiles | Base, collector, analysis and TUI metadata/hash/dependency boundaries pass; 161 installed source files match. |
| Installed CLI/services | All four portable profiles pass; archive flows pass for collector/analysis/TUI. Analysis includes controlled external denial and durable recovery. Strict real-host positive remains failed. |
| TUI | All 23 forms, navigation, real services, cancellation and actual terminal PTY checks pass. |
| Dependency audit | Seven existing profiles pass with zero known vulnerabilities; Textual is included in the audited dev closure. |
| Fresh SonarQube after user-approved review | **OK, exit 0**: 83.7% new coverage, 0.0% duplication, zero new/open issues and zero Security Hotspots. Exactly seven reviewed False Positive dispositions; no gate changes. |
| Semgrep 1.179.0 | 151 official Python rules, 163 production/script files, one warning, zero errors; warning reviewed below. |
| Trivy 0.75.0 | Zero CVE, secret or supported configuration findings; 57 main-lock and 26 CPU-runtime package entries including dev dependencies. |

The final portable gate reaches and passes packaging/workflow verification and
the unchanged audit. Its 32 skips are the existing 31 F21/F22 host checks and
the new F24 real-worker positive. These require explicitly authorized
system-manager qualification and, where applicable, the provisioned real CPU
runtime. Their skip status is not qualification evidence and remains a delivery
blocker.

Both wheel and sdist reproduce byte-for-byte:

- Wheel: `325e435f1e99cd5e9cf4b4c16d34ba09409db165cd8100130152f8962d99f62d`.
- Sdist: `70071460b7865e6403ef7d3e128d9ad8fdbf6eeadbeac703b9e0d0a231471b03`.

The successful installed collector reports received, durably committed,
normalized and published offsets all equal to 2 and complete shutdown. Real
collection retains any host CLOCK fault; controlled fresh-clock fixtures require
no faults, and missing/stale fixtures explicitly require clock degradation.
The separate denied-worker scenario passes while the strict success scenario
continues to fail. No assertion was replaced or skipped to reinterpret denial as
successful execution.

## Earlier Fedora authorization blocker

The existing noninteractive system-manager launch is denied under the current
ordinary user. A harmless fixed `systemd-run --system --no-ask-password` probe
also fails with access denied requiring interactive authentication. Actual
analysis admission remains durable, the job becomes failed (`provider_failed`),
and no artifact is accepted. F21/F22 launch/isolation policy is unchanged.
This is an environment authorization blocker exposed by a new positive test;
it does not qualify the new successful analysis path. Required positive tests
must run on an existing appropriately authorized and provisioned worker host.
No new systemd units, polkit grants or fallback in-process execution are provided.

Semgrep's raw warning is
`python.django.security.injection.request-data-write.request-data-write` at the
immutable job-plan writer. It is a generic Django/request-write match: the
request is a local typed DTO; the generated filename is a SHA-256 digest under
a protected directory, and the canonical plan is bounded to 64 KiB with at most
1024 plans. No-follow directory descriptors, exclusive temporary creation and
no-replace publication prevent arbitrary destination selection or replacement.
There is no HTTP-header/log sink. Independent source review classifies this
match as not actionable; the raw finding remains recorded and no rule is
suppressed. Semgrep CE does not provide Pro inter-file analysis.

Trivy database UpdatedAt: `2026-10-11T19:06:46Z`; downloaded
`2026-10-11T22:00:05Z`. There were no supported configuration targets. This does
not establish safety of unsupported configuration, the OS or model artifacts.
No code/findings were uploaded by these local scanners.

SonarQube's exact issue review and user-authorized dispositions are recorded below.
The user explicitly approved the seven listed False Positive dispositions; each
now has its reviewed rationale on the server. The repeated full analysis exits
zero and the gate is OK. Quality thresholds, exclusions, severities and security
policies remain unchanged.

## Technical challenge and API evidence

SOFA's [shield/reference discussion](https://agents.stackoverflow.com/tils/e688f7a2-27fe-4874-8033-08376133ea16)
was consulted as a lead: shielding alone neither establishes owner termination
nor guarantees result observation. Context7 `/python/cpython` and Python 3.12
[task/shield documentation](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.shield)
confirm retaining strong references and observing completion. Existing services
retain futures/tasks and join actual owners; repeated-cancellation tests now also
hold SFTP cleanup open and prove it finishes before cancellation propagates.
Community examples printing raw prompts/errors were not adopted. No SOFA posts,
votes, replies or verifications were published.

The [threading exception contract](https://docs.python.org/3.12/library/threading.html#threading.excepthook)
confirms uncaught worker-thread exceptions can reach stderr. A separate sentinel
probe demonstrated why rethrowing after transferring failure through a Future
would expose exception text without improving delivery to the application caller.
Textual behavior was exercised against installed 8.2.8 with Pilot and terminal
PTY, rather than assuming future package versions have identical behavior.

## Evidence and remaining work

Private raw reports, canonical installed-command records, artifact/source
manifests and test logs are retained under the ignored local F24 evidence area.
The task report is `docs/task-reports/F24.md` and is Git-ignored.

Before delivery: prepare the verified local commit and concrete publication
payload. Successful full-gate, real-source, model, applicable host and authorized
Sonar results are recorded above. The user explicitly requires approval before a
push. Keep auto-merge disabled. KER-28 was Backlog at the latest read; it has not
been moved to In Review before publication.
Live backfill is supported by the actual exchange checks recorded above.

## User-authorized final Sonar review

Before authorization, the gate was FAILED with seven open violations. The user
then explicitly approved exactly the seven listed semantic False Positive
records. All seven were marked and independently re-read as FALSE_POSITIVE;
reviewed rationale was attached through the existing authenticated local API.
The subsequent full analysis exits zero and the gate is **OK**. The fresh final
snapshot also passes: new coverage 83.7% (threshold 80%), duplication 0.0%
(threshold 3%), zero new/open violations
(threshold zero), zero Security Hotspots. Source repairs reduced the initial
71-violation checkpoint; no thresholds, exclusions or suppressions changed.
These are new F24 review records, distinct from previous F23 authorization.

| Issue key | Rule | Final source location | Source-backed rationale |
| --- | --- | --- | --- |
| `3ab85924-1530-4310-bd0b-26ae4a2661bf` | `python:S7497` | `src/scryntic/workflows/context.py:67` | CancelledError is retained through repeated cancellation and re-raised after the actual owner settles; intentional transport cancellation is distinguished. |
| `3998d578-e485-4abf-a567-b09e058e53cc` | `python:S5754` | `src/scryntic/workflows/jobs.py:193` | Worker-thread failure is delivered through ready/done Futures after cleanup; rethrowing in the private thread would invoke the raw stderr hook. |
| `249cd866-ebda-490b-a636-a71074b05e61` | `python:S5754` | `src/scryntic/workflows/jobs.py:206` | Worker-thread failure is delivered through ready/done Futures after cleanup; rethrowing in the private thread would invoke the raw stderr hook. |
| `4228b9a3-1751-491e-a957-a01f60490ee8` | `python:S5754` | `src/scryntic/workflows/jobs.py:217` | Worker-thread failure is delivered through ready/done Futures after cleanup; rethrowing in the private thread would invoke the raw stderr hook. |
| `815a8828-1a60-4a0f-a180-ac6f9aba0fef` | `python:S5754` | `src/scryntic/workflows/jobs.py:226` | Worker-thread failure is delivered through ready/done Futures after cleanup; rethrowing in the private thread would invoke the raw stderr hook. |
| `764d3fa4-74a7-436f-82a0-b56cd51264de` | `python:S7497` | `src/scryntic/workflows/local.py:211` | CancelledError is retained through repeated cancellation and re-raised after the actual owner settles; intentional transport cancellation is distinguished. |
| `eb6dd2ba-b6ef-4c70-8caa-1391a1fec935` | `python:S7497` | `src/scryntic/workflows/synchronization.py:256` | CancelledError is retained through repeated cancellation and re-raised after the actual owner settles; intentional transport cancellation is distinguished. |

The four thread-owner locations are covered by eight initialization/cleanup
failure cases; an additional owner-closure test passes. Context and SFTP tests
now cancel cleanup twice while an event deliberately holds the actual owner
open, then require settlement before CancelledError delivery. Finite child tests
cover repeated cancellation, forced deadline and actual process reaping.
Immediate rethrow would abandon required cleanup; removing these catches or
hiding the warnings is not an acceptable correction. The SFTP join was extracted
without changing behavior to resolve its genuine complexity finding.

[Quality policy](QUALITY.md) requires a separately authorized review mechanism
for server issue dispositions. The explicit user approval was applied only to
these seven **False Positive** records, with the rationale above. The supported
endpoints were verified through `/api/webservices/list` before writing. Server
readback and the unchanged gate passed for that analysis snapshot. Subsequent
verification-only changes require a fresh analysis of the snapshot above.
This review does not resolve the separate worker-launch qualification blocker.

## Portable versus real-host verification

The first full gate required external model-worker success unconditionally.
The user's independent ordinary-user repetition failed identically: `jobs run`
returned exit 7, durable state `failed`, reason `provider_failed`, and no artifact.
A read-only noninteractive `pkcheck` for
`org.freedesktop.systemd1.manage-units` returned exit 2 for UID 1000. Neither
interactive authentication nor a permission change was requested.

The user's flagged repetition also fails at `jobs run` (exit 7). A separate
ordinary-UID diagnostic of the actual strict workflow reached `CPUWorker._launch`
and classified its bounded stderr as `Access denied`; launch did not succeed.
Only fixed booleans were retained, without raw stderr or payloads. Evidence:
`worker-denial-diagnostic.json` and `worker-denial-diagnostic.log`. The diagnostic
used a fresh temporary installation, retained the real launch/cleanup contract,
and changed no installed source, system-manager policy or privileges.

Verification now follows the existing opt-in host-check pattern. Portable
analysis injects a fixed external launch denial only inside its isolated verifier
subprocess and checks actual admission, failure/recovery, sanitized diagnostics
and absence of an artifact. There is no production switch. The positive pytest
requires `SCRYNTIC_F24_HOST=1`; the installed-wheel `analysis-host` role always
requires actual success. The full gate runs both when that flag is set. Skipping
this positive check explicitly leaves host execution unqualified and prevents
delivery; a portable pass cannot satisfy the mandatory positive acceptance check.
See [qualification commands](../operations/F24_USAGE.md#installed-application-checks).

The initial portable rerun exposed four test-fixture failures: three asserted no
host clock fault, and one observed a normalization fault before the committed
counter cache refreshed (1954 passed, four failed, 32 skipped). Recorded source
fixtures now inject fresh evidence; real subprocess collection preserves and
allows only genuine CLOCK degradation while retaining all durable counters and
owner-release assertions. New missing/stale evidence cases require CLOCK,
UNKNOWN, DEGRADED and false readiness. The metadata-barrier test waits for the
committed cache before drain; source ordering guarantees accepted raw precedes
that barrier and the fixture refreshes its cache after stopping intake. Focused
tests pass, including ten repeated barrier checks. Runtime clock policy and model
isolation are unchanged. The final full portable gate passes with 1960 tests,
32 explicitly unqualified host skips, installed packaging and all seven audit
profiles. Fresh Semgrep and Trivy also pass their scans with the same reviewed
warning and no new findings; the fresh Sonar gate is OK. An independent read-only
source review found no concrete defect in these five verification paths.

Final evidence: `check-portable-final.log`, `negative-portable.log`,
`frozen-portable-source.json`, `sonar-portable-final.log`,
`sonar-portable-final-status.json`, `semgrep-portable-final.json`, and
`trivy-portable-final.json` in the private ignored evidence area. No external
publication or tracker transition has been performed.
