# F20 / KER-24 validation evidence

Date: 2026-10-05. Base: `main` at
`09ca623af3a94b7e48c18077ffc6bae5d7d08916`; implementation branch:
`ker-24-f20-provider-jobs`. KER-13/F09, KER-22/F18 and KER-23/F19 were verified
Done and present in this base. Scope and acceptance criteria came from KER-24
and the approved ARCHITECTURE.md. See [JOBS.md](JOBS.md) for operator/API contracts
and [F20_PLAN.md](F20_PLAN.md) for design decisions.

## Contract evidence

| KER-24 requirement | Implementation and focused evidence |
| --- | --- |
| Known origin/revision/license/use/loading approval | Exact review digest, explicit license/use grants and safe built-in loading profile; `tests/jobs/test_admission.py` rejects unknown/missing/mutable metadata and unsafe loading even with an exact review grant |
| Versioned local/remote jobs | Closed 1.0 request/attempt/response envelopes, canonical bounded primitives; `test_codec.py` round-trips owned contracts and rejects identity/version/shape/nonfinite/replay/size defects |
| Shared local/remote conformance | Persistence and trend fixtures use identical envelopes and result checks; `test_http.py` exercises both paths, terminal idempotence and current-attempt response fencing |
| Durable states and safe recovery | Private pinned WAL/FULL journal, conditional transitions and exclusive ownership; `test_store.py` checks restart interruption, fresh attempt/token, stale completion rejection, retry quota, deadline/clock rollback and terminal preservation |
| Durable cancellation/deadlines | `test_execution.py` tests caller/explicit cancellation, shutdown, expiry and late output; suppressed child cancellation cannot become success, and early provider timeout remains distinct from job expiry |
| Result acceptance and crash convergence | Application validation and immutable artifact installation precede fenced success; crash tests cover before/after success and reuse one immutable object without duplicate accepted success |
| Hostile/oversized HTTP | Redirects, declared and actual oversized bodies, compressed/malformed outputs and slow peers fail; `test_http_inbound.py` verifies raw compressed request bytes reach rejection without parser inflation |
| Preserve F18 provenance and F09/F19 contracts | `test_inputs.py` uses real publication/import/F18 recipe and restricted inspection; accepted artifact keeps the exact DatasetRef and unchanged pins/manifest. Gapped/unfinalized/frequency-mismatched inputs fail. Existing forecast/contracts/slice and all replay tests pass |

Focused tests were written and run red before initial modules and the critical
lifecycle fixes. Three extra regressions exposed and repaired advancing-clock
submission retry conflicts, early provider-timeout misclassification and suppressed
caller cancellation. A final manual metadata check also reproduced and repaired an unknown origin masked by surrounding whitespace/case. An independent read-only review found one additional inbound
HTTP parser decompression gap; a small safe gzip regression reproduced it before
`AppRunner(auto_decompress=False)` repaired the boundary. Current aiohttp source,
Context7 and [primary server documentation](https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.AppRunner)
confirmed the relevant default and supported option.

Real SQLite authorizer fault injection also verified that cancellation write and
rollback failures poison the owner, never report a false cancellation/success,
and reopen as interrupted without an accepted artifact. No gate or state-file
boundary was relaxed to accommodate sandbox ownership restrictions; boundary and
HTTP tests ran with actual host filesystem/loopback semantics.

## Project gates

Pinned uv 0.12.13 and Python 3.12.14, unchanged locked dependencies:

| Command | Result |
| --- | --- |
| `bash scripts/check.sh` | PASS: lock/sync, Ruff, format, strict mypy, coverage/pytest, installed packaging profiles and six-profile dependency audit |
| Full pytest under coverage | **1,518 passed**, 95.44 seconds |
| Ruff / mypy | 285 formatted files including this evidence document; no mypy issues across 233 source files |
| `uv run --locked --no-sync python scripts/check_negative.py` | PASS: all seven disposable negative controls |
| `git diff --check` | PASS |

Coverage XML, database, scanner working directories and raw reports were kept in
private temporary validation storage outside the repository. Source/config hashes
recorded before final gates/scans remained unchanged afterward.

## Security and quality scans

| Tool and scope | Final result |
| --- | --- |
| SonarQube 26.9.0.129388 / SonarScanner 8.0.1.6346; fresh full Scryntic checkout analysis with final coverage | **Quality Gate OK**, 0 OPEN/CONFIRMED issues, 0 new violations, 0 Security Hotspots; bugs/vulnerabilities/code smells all 0 |
| Sonar new-code coverage / duplication | **89.2% / 0.0%**, unchanged thresholds 80% / 3% and zero new issues |
| Sonar overall coverage | Combined 81.5%; line 84.9%; branch 71.0%; duplication 0.0% |
| Semgrep 1.179.0 CE, 151 official local Python security rules, 23 explicit changed/relevant Python targets including new tests | **0 findings, 0 scanner errors**, all target lines parsed; metrics, cloud and Pro features disabled |
| Trivy 0.75.0 filesystem: vulnerability, secret and misconfiguration scanners; all severities and development dependency groups | **0 CVEs, 0 secrets** across all 54 `uv.lock` package records. No supported IaC targets were detected, so IaC checks are not a configuration qualification |

Sonar per-file coverage was inspected, not just the gate: job journal line/branch
90.3%/72.1%, coordinator 94.8%/80.4%, HTTP 87.0%/65.8%, codec 93.1%/75.0%.
The configured Community Build exposes only its `main` analysis slot. The analyzed
checkout was the F20 implementation branch; using that server slot does not imply
that this branch was merged. The pre-publication final analysis timestamp was
2026-10-05 15:19:39 UTC. Publication and commit/CI evidence are linked from KER-24
and the pull request.

Initial Sonar findings were checked against current source. F20 clarity fixes
separated dispatch from timeout/task cleanup, separated persisted-field validation,
reused fixed JSON/SQL constants, preserved fatal exceptions after failed rollback,
and moved fixture setup outside exception assertions. The owner explicitly
authorized two minimal pre-existing writer/test readability corrections and
three evidence-backed False Positive dispositions:

- `b3422de8-f5e3-4c5e-8b52-7fd514f5a424` / S7503: bounded built-in fake intentionally implements the shared asynchronous provider port without blocking IO.
- `3576498a-4d4e-47ec-bedd-b2acfbf8672e` and `495c7a11-bba3-4fb4-b69b-9dcb02245cad` / S5332: duplicate warnings for the required fixed-loopback HTTP test fixture, without arbitrary endpoints or credentials.

Each disposition has an English evidence comment in Sonar. The direct API changes
used the explicitly authorized existing private connection; the MCP remains
read-only. Gates, exclusions, rules, severities and security boundaries were
unchanged. No suppression was added to source/configuration.

Trivy used the local DB updated 2026-10-05 07:14:49 UTC. Its final scope excluded
ignored private/tool directories and obsolete `.worktrees` checkouts, not current
project dependencies. An initial broader exploratory scan found three CVEs in
each of two old ignored worktree lockfiles; those were not dependencies of the
resulting checkout. The final current `uv.lock` scan and six-profile audit were
clean. Semgrep CE supplies local rule analysis; no inter-file Pro analysis or
cloud validation is claimed. These are tool/version/database snapshots, not a
guarantee against every vulnerability.

## Remaining limits

- Trusted deterministic synthetic fixtures only: model weights, loader integrity
  and resource/device isolation are not qualified here (F21/F22).
- Remote transport is an explicitly authorized fixed loopback test double, not
  an arbitrary endpoint, credential or TLS deployment integration.
- The journal provides at-most-once accepted success, not exactly-once external
  computation; remote cache retention is bounded and in-memory.
- Explicit dispatch/resume and state-access deadline enforcement are intentional;
  background scheduling/daemon lifecycle and automatic history/orphan retention
  are separate work.
- The operator supplies review evidence and policy grants through trusted
  application wiring. Registration, a schema match or provider self-description
  never constitutes approval.

KER-24 is left **In Review**, not Done, after validation and an unmerged PR are
published. No F21/F22 or financial execution scope was implemented.
