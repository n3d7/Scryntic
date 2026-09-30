# F16 / KER-20 validation and SonarQube evidence

Recorded 2026-09-30 for `ker-20-hostile-imports`, based on main
`6856b6ba434f0258d567b06fd392c5157acdc03b`. These are working-tree measurements
before publication, not qualification of every Linux host or native decoder.

## Fresh local validation

- Qualified host: Fedora 44, Linux 7.2.7 x86_64, Python 3.12.14,
  Bubblewrap 0.12.0 and operator-provisioned libseccomp. The explicit
  `uv run --locked --no-sync python scripts/check_import_boundary.py` probe passed.
- `uv run --locked --no-sync pytest -q tests/imports`: 43 passed. Host controls
  are exercised, not conditionally skipped when unavailable.
- `bash scripts/check.sh`: passed with pinned uv 0.12.13, strict mypy (164 source
  files), Ruff lint/format, 1135 tests, reproducible packaging and installed
  base/collector/analysis profiles, lock/dependency closure/resource checks and
  dependency audits (no known vulnerabilities in the audited closures).
- `uv run --locked --no-sync python scripts/check_negative.py`: seven expected
  rejections (test, lint, format, type, lock, resource, audit-unavailable), each
  exit 1 with source inputs unchanged.
- Read-only independent review found two recovery defects; both were repaired
  and rechecked: quota accounting now charges only absent objects, and recovery
  removes validated temporary staging links after a crash between link/fsync and
  unlink. Crash recovery and an exact-quota retry after a failed SQL transaction
  now pass. Durable digest-named orphans remain bounded and reusable.

Hostile probes cover duplicate/corrupt/noncanonical JSON, unsupported codecs and
schemas, rehashed corrupt native payloads, traversal, symlink/hardlink/FIFO/socket/
directory inputs, replacement after pinning, mutation during copying, sealed
snapshot immutability, logical decode limits, invalid/flooded worker messages,
timeouts and descendant-held pipes, unavailable controls, SQL rollback, catalog
path substitution, corrupt stored objects and fresh restricted reinspection.
The worker's effective-control probe checks hidden host authority, sanitized
environment, pipe-only descriptors, denied network/process/filesystem operations
and hard address-space limits before native decoder loading.

## SonarQube analysis and disposition

Full SonarScanner analysis completed on SonarQube Server 26.9.0.129388. Compute
task: `4158f8d4-5f21-4781-9788-2dd7284b19cc`; result snapshot:
`2026-09-30T20:10:50.122607+00:00`. Project key is `Scryntic`. The configured server
exposes only its default `main` analysis context: this scan analyzed the local
feature working tree in that context, not a published main commit or PR analysis.

The gate is **ERROR**, not green: 110 new violations, 0% reported new coverage,
0% new duplication; 295 open issues in the complete paginated issue snapshot.
The new-code baseline is the previous-version analysis dated
`2026-09-28T22:37:06+0000`, so its scope includes changes from F12–F15 as well as
F16. No coverage report was supplied; 0% is not the result of a coverage run.
The server's analysis-history and security-hotspot endpoints returned HTTP 403
for the configured read-only identity, so hotspot/history review is unavailable.

Affected findings were inspected against current source, architecture and tests.
Actionable cleanup grouped repeated whitelist export literals, split effective
control probes by authority, centralized repeated syscall failure text and
replaced an IPC validation assertion with an explicit fail-closed check.
The final snapshot retains these three findings within `src/scryntic/imports`:

| Rule / finding | Disposition |
| --- | --- |
| S5754 / `805fca79-607b-4664-9210-dc15126db2b1`, bootstrap broad exception handler | Intentional fail-closed terminal boundary: sanitize every failure and hard-exit, including interruption; never continue native decoding under partial controls. |
| S930 / `120c246a-8f22-4a26-b866-f2dc1471bbd1`, SQLite `autocommit=True` | Analyzer false positive: supported by pinned Python 3.12, verified against official CPython documentation through Context7 and the transactional tests. |
| S3776 / `ca024992-3791-42e2-9d27-a4d75d5049a3`, bounded subprocess supervisor | Retained centralized deadline/pipe/kill/reap cleanup; independently exercised by flood, timeout and descendant-pipe probes. Mechanical restructuring would obscure coupled failure handling. |

Extracted F11 native validators retain their existing schema/digest/domain checks
and associated complexity warnings. Lazy static API exports retain typing and
make import-catalog/coordinator loading independent of native decoder loading.
Test exception-expression warnings were reviewed in their fixtures; valid setup
arguments do not substitute for the rejected operation. Unrelated findings and
server issue statuses/gate settings were not changed to obtain a green result.

## Cross-checks and limits

Stack Overflow for Agents supplied bounded second opinions on process groups,
inherited pipes, namespaces and filesystem authority; [IMPORTS.md](IMPORTS.md)
links the inspected posts and authoritative references. Its advisory-flock example
was not copied: the catalog keeps a stable lock inode and never unlinks the lock
on release. The kernel releases the advisory lock on descriptor close. Context7
CPython/Bubblewrap documentation and current source were used to verify material
decisions; races, IPC, deadlines and resource failures were tested directly.

The native decoder remains an untrusted process; bounded conformance admission
never grants future parsing trust. Root, a malicious owning UID, the kernel,
operator-provisioned runtime/code mounts and storage are outside this boundary.
These tests do not prove absence of kernel/native vulnerabilities, physical
power-loss durability, portability or F21 model/GPU qualification. F17 transport,
enrollment and retained chain anchors are outside F16. A quality gate is never
proof of isolation. CI host qualification is recorded separately by the PR run.
