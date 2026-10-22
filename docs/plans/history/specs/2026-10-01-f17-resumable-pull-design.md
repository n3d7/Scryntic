# F17 / KER-21 resumable authenticated pull

The operator delegated design and implementation of the already approved F17
contract. ARCHITECTURE.md's server-to-workstation synchronization section and
KER-21 govern this design. No new authority or architecture change is proposed.

## Contract and boundaries

A workstation enrolls one named endpoint with an out-of-band Ed25519 host key,
numeric IP, port, read-only username, published-artifact root, producer and epoch.
Enrollment explicitly chooses genesis or supplies the exact canonical checkpoint
manifest and accepts unavailable earlier coverage. There is no trust-on-first-use,
SSH agent/key discovery, forwarding, remote command or remote write API.

The AsyncSSH 2.24.0 adapter owns the synchronization FileCredentials capability.
It receives the key through the existing protected-file reader, authenticates
only to the enrolled host identity, and opens only SFTP. Its finite connection,
operation and pull deadlines, bounded listings and reads apply to every request.
The adapter never passes credentials, SSH state or network descriptors to F16.
Numeric IPs avoid uncontrolled resolver waits. The server must separately enforce
its dedicated read-only/jail policy; F27 deployment automation is out of scope.

Discovery walks the existing F11 archive/manifests namespace and validates each
committed slot against its generated epoch digest, twenty-digit sequence and
canonical manifest hash. Empty crash-created slots are harmless. It compares
every retained accepted manifest, requires consecutive predecessors and matching
checkpoints, and rejects unexpected epochs, rewritten/truncated history or gaps.
An optional archive/head.json is only a hint: direct discovery and bounded retry
govern progress, and stale/malformed/absent hints only affect freshness reporting.
Publication sequence, never event time, discovers late corrections.

Private descriptor-pinned staging preserves bounded per-digest partial files.
Restart resumes from a verified local length; final exact length and SHA-256 are
mandatory before F16 import. Invalid partials may restart that object but never
reset accepted history. Generated names, no-follow inode pinning, regular-file/
ownership/link/mode checks, quotas, fsync and no-replace publication protect local
storage. Missing objects are transferred; locally cached objects are hash-checked
again. Untrusted SFTP metadata cannot grant local path authority. SFTP v3 cannot
prove remote pathname stability against a malicious server; final hashes and the
server-side jail are the applicable boundaries.

## Atomic accepted state

PullCatalog extends ImportCatalog using a narrow protected registration hook.
F16 still validates every new acceptance in its restricted worker. The hook adds
the exact retained manifest and compare-and-swap anchor in the same SQLite FULL/
WAL transaction as the imports row. Plain F16 acceptance never creates enrollment
or advances transport anchors. Bootstrap manifests are trust/coverage boundaries,
not assertions that unavailable objects passed F16. Accepted catalog entries never
allow later native decoding in the transfer/coordinator process.

Only explicit local operator calls enroll, authorize a new epoch or restore an
anchor backup. Old epoch anchors and manifests remain immutable. Approval of a
new genesis preserves the old anchor and checks known global input continuity.
Canonical bounded backups include endpoint pins, bootstrap/accepted anchors and
all retained manifests, never credentials. Restore is transactional, idempotent,
refuses conflicts or rollback of existing anchors, and never installs data as
validated merely because the trust backup names it. Operators preserve independent
backups; a deliberately supplied stale backup on an empty workstation cannot be
distinguished cryptographically from its historical state.

## Implementation choices and validation

AsyncSSH was selected over Paramiko/OpenSSH batch-output parsing for typed APIs,
async deadlines and bounded SFTP operations. It is pinned in the workstation
analysis dependency group, with EPL-2.0 licensing and native cryptography reviewed
in the supply-chain inventory. Base/collector profiles do not acquire SSH tooling.

Tests exercise real local SSH/SFTP authentication and host-key mismatch, denied
writes/shell/forwarding, interruptions at chunk and registration boundaries,
repeated convergence, missing-object reuse, late corrections, all history attacks,
stale hints, explicit checkpoint/epoch approval, durable reopen and backup/restore,
unsafe local files and quotas. Fresh project gates, F17-attributable Sonar findings
and a Codex Security diff scan against origin/main complete qualification. No
repository-wide/deep scan or unrelated baseline cleanup is authorized. KER-21
ends In Review with a pushed PR; no merge or auto-merge.
