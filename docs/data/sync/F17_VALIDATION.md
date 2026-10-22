# F17 qualification evidence

KER-21 is implemented on `ker-21-f17-resumable-pull`, based on main
`f30c4a11626a62ce0fe07f2f6610ec6adb8cfc63`. This evidence qualifies the workstation
API, not F27 deployment, F29 retention or model-worker isolation. See the
[operator guide](F17_PULL.md).

## Contract probes and project gates

Commands use CPython 3.12.14, uv 0.12.13 and the locked dev/collector/analysis
profiles, on the real Linux host with the existing F16 controls available.

- `.venv/bin/python -m pytest tests/sync -q --tb=short`: **80 PASS**.
- `bash scripts/check.sh`: **PASS**, including 1236 tests, Ruff, formatting,
  strict mypy, coverage XML, installed-profile packaging and dependency audits.
- `.venv/bin/python scripts/check_negative.py`: **7 PASS**;
  test/lint/format/type/stale-lock/missing-resource/unavailable-audit failures
  were rejected without changing their disposable inputs.
- Independent scoped source review and four focused regressions: **PASS**.
  The review found and repaired transport-anchor progression after a prior plain
  F16 import, and synchronous work crossing a cooperative deadline.

Real AsyncSSH server tests cover exact host pin before authentication, isolated
public-key authentication, refused shell/forwarding/write operations, unsafe
credential files and secret redaction, traversal/symlinks/special entries,
bounded reads/listings, handshake/auth/read deadlines, cancellation and oversized
SFTP packet headers before body allocation. An end-to-end pull uses real F11
objects, real SFTP and the actual restricted F16 decoder.

Chain probes cover repeated pulls, interrupted byte-offset resume with complete
hash validation, bounded batches, a later correction to the same candle interval,
stale head hints, missing/corrupt objects, rewritten/truncated/gapped history,
wrong predecessors, unexpected/approved epochs, checkpoint bootstrap and restored
history rehydration. Catalog probes cover durable reopen, atomic rollback,
terminal interruption, failed rollback poisoning, expired-decoder non-registration
and bounded conflict/rollback-resistant trust backups. Filesystem probes cover
symlink/FIFO/hardlink partial files, name replacement before publication, quota
rejection and recovery after linking complete bytes but before unlinking the part.

## External cross-checks

Context7 was used for the exact AsyncSSH connection/SFTP APIs and CPython SQLite
transaction/backup APIs, then checked against installed AsyncSSH 2.24.0 source,
the locked Python runtime, source contracts and tests. Primary references:
[AsyncSSH API](https://asyncssh.readthedocs.io/en/latest/api.html),
[Python 3.12 SQLite](https://docs.python.org/3.12/library/sqlite3.html),
[SQLite transactions](https://www.sqlite.org/lang_transaction.html).

Stack Overflow for Agents provided second opinions:

- [Download resume](https://agents.stackoverflow.com/questions/7ccb2f23-72c8-49ad-b02f-f96a70b713e1):
  retain partial bytes and require final whole-file integrity. HTTP Range/ETag
  details were not treated as SFTP guarantees.
- [SQLite WAL backup](https://agents.stackoverflow.com/tils/2dac4041-49df-4c1e-82fa-938c118ba4a2):
  copying only an open database's main file is not a consistent backup. F17
  exports bounded logical trust records through the catalog owner instead.
- [POSIX locking](https://agents.stackoverflow.com/questions/7255a13f-4da9-4d62-bcc5-92b430c07549):
  inspect lock lifetime and inode identity, including corrections to replies.
  F17 retains the existing stable catalog lock and pins staging descriptors.

An exact AsyncSSH host-pinning search produced no usable SOFA recipe. The pin,
packet framing and timeout decisions rely on primary APIs/source and executable
SSH probes rather than inferred community guarantees.

## SonarQube review

Full project analysis, fresh branch/line coverage XML and the existing
`Scryntic` server project were used. The server provides only its default main
analysis context; this is a local F17 working-tree analysis, not branch/PR
decoration. The existing project version/new-code baseline was retained rather
than reset to obtain a green gate. Uncommitted files produce missing-blame
warnings; those do not invalidate the imported coverage report.

The pre-F17 server snapshot had 119 unresolved findings and gate ERROR
(new coverage 75.5%, 56 new violations). Initial F17 analysis added 35 findings.
The review repaired cohesive catalog/discovery/transfer/listing complexity,
repeated error strings, a redundant TimeoutError/OSError catch, Python 3.12
generic syntax and ambiguous/composite test argument/assertion setup.

Intentional behavior is retained: ASCII `[0-9]` excludes Unicode digits;
rollback catches terminal errors to poison an unusable owner and then propagates
the original failure; `dataclasses.replace(Enrollment, ...)` retains its exact
runtime type, confirmed by source, tests and strict mypy. Async coroutine creation
inside a timeout `pytest.raises` does not execute its body; staging context-manager
probes intentionally include enter/write/finish/exit failure surfaces. These are
reviewed dispositions, not server suppressions. No thresholds, exclusions,
issue statuses, native-worker instrumentation or unrelated baseline code changed.

Final analysis: **2026-10-01 01:29:40 UTC**, default main server context,
**133 unresolved findings**, including **14 reviewed F17 findings**. Gate
**ERROR**: new coverage **78.3%** (required 80%) and **70 new violations**
(required zero); duplication **0%**. Overall Sonar coverage is **79.4%**,
line coverage **81.6%**, branch coverage **72.3%**. Analysis-history and security-
hotspot endpoints return **403**, so those views were not independently inspected.

The remaining F17 findings comprise eight exception-test suggestions, two
incorrect `dataclasses.replace` type inferences, the intentional rollback and
ASCII checks, a cohesive bounded discovery loop with complexity 19, and repeated
fixed `History limit` messages at distinct quota guards. The last two are reviewed
minor maintainability tradeoffs, not confirmed correctness/security defects.
Their continued presence is explicit; the global gate is not treated as green.

## Scoped Codex Security review

`security-diff-scan` reviewed only the F17 working-tree change set against
`origin/main` (`f30c4a1`), with a fresh scoped threat model. Scan
`4a357448-2b9c-4700-8563-d2c8ccd043e4` was sealed at
**2026-10-01 15:14:07 UTC**: **0 findings, 0 deferred candidates** and complete
coverage of all **10 managed source/configuration inventory items**, including
untracked sync source. Changed test, lock and documentation support was inspected
separately; no deleted production files existed. No repository-wide/deep scan ran.

The managed local report is retained under
`~/.codex/state/plugins/codex-security/scans/Scryntic/`
`f30c4a11626a62ce0fe07f2f6610ec6adb8cfc63_20261001T012938Z_38j40bax/report.md`,
with sibling `exports/results.sarif`. This source-backed review corroborates
the probes and boundaries; it does not independently prove host/server isolation.

## Remaining operational limits

SFTP v3 pathname checks do not provide a remote inode snapshot. Exact pins,
generated names and final hashes detect changed bytes; the server jail/read-only
account is an operator prerequisite. The bounded SFTP reader relies on the
inspected pinned factory API and must be requalified on upgrades.

Synchronous bounded filesystem/F16 work can delay a response beyond the cooperative
network wall deadline. Monotonic checks between units and before post-decoder
registration reject expired work without advancing its anchor; this is not a
hard real-time bound on kernel I/O. Independent backups contain trust/history,
not object data or credentials. Bootstrap does not recover expired historical
objects. Host/dependency compromise, physical power loss and dishonest storage
are not established safe by these tests, Sonar or a security scan.
