# Hostile workstation imports (F16 / KER-20)

`ImportCatalog` owns conformance admission and opaque local storage. It does not
enroll a server, authenticate transport, manage chain anchors or decide whether
market data is true. Those responsibilities remain in F17 and later consumers.
The collector's F11 readers are trusted local-publication readers, not import APIs.

## Workstation service and provisioning

Provision pinned Python and `uv sync --locked --no-default-groups --group analysis`.
PyArrow 25.0.1 is the native decoder profile; remote identifiers cannot load code.
The F10 framed benchmark candidate is not an approved production import codec.
No runtime download/install occurs. General CLI integration belongs to F20.

On a qualified Linux x86_64 host, provision Bubblewrap and libseccomp through the
OS package manager. The fixed launcher requires unprivileged user/mount/network/
IPC/PID/UTS/cgroup namespaces, user-namespace nesting controls and seccomp/rlimits.
There is no weaker fallback. Bubblewrap is a mechanism: fixed application arguments
define its policy. The host qualification command is
`uv run --locked --no-sync python scripts/check_import_boundary.py`.

```python
from scryntic.configuration.paths import Installation
from scryntic.imports.catalog import ImportCatalog

# incoming: approved owner-only directory with regular files named by SHA-256.
# manifest_bytes: bounded opaque canonical JSON; receipt: fresh F12 ClockSample.
with ImportCatalog(Installation.workstation()) as catalog:
    reference = catalog.accept(manifest_bytes, incoming, receipt)
    catalog.inspect(reference)  # always snapshots and decodes again
```

The API returns an owned manifest reference, never an Arrow batch, pickle, worker
filename or a path authorizing in-process native parsing. New analytical consumers
must remain behind a restricted decoder. A catalog lookup is not parsing trust.

## Admission and storage

The bounded canonical F11 manifest parser refuses duplicate keys, unsupported
schemas/algorithms, noncanonical bytes, descriptor/identity/count inconsistency
and excessive claims before native parsing. Only raw/normalized Parquet 1.0 with
`parquet-pyarrow-v1` is admitted. Local destinations derive from validated SHA-256;
manifest text never chooses paths, code, extensions or SQL.

Descriptor-relative `O_PATH|O_NOFOLLOW` pins an entry before type/owner/mode/link/
length checks. Only a regular inode is reopened through its pinned `/proc/self/fd`
handle. FIFO/socket/device/symlink replacements cannot trigger reading. Bounded
copies check before/after inode metadata and hashes, then use Linux memfd seals
against writing/truncation/growth. A replaced name may leave the already pinned
valid inode usable; corrupt mutable bytes cannot become its immutable snapshot.

The worker receives read-only snapshots. Native loading follows hard controls and
effective-control probes. Existing F11 rules verify exact Parquet schemas/metadata/
compression, bounded logical size/count, domain types/times/order, identities,
partitions, normalized outcomes/instrument semantics and raw/provenance/ordered
digests. Raw payloads remain original evidence, including normalization rejections;
admission does not certify market truth.

The coordinator compares the complete bounded canonical primitive result with
expected metadata. Unexpected fields, duplicate keys, boolean/integer substitutions,
noncanonical numbers, arbitrary object encodings and trailing bytes reject it.
Both output pipes have independent byte ceilings. Stderr is bounded and discarded,
never a protocol or logging channel. Denied jemalloc background-thread creation
may emit a diagnostic without granting thread authority.

Private `state/imports` has one lock/connection owner and SQLite WAL/FULL. Opaque
objects are installed without replacement and file/directory-fsynced before
registration. Manifest and workstation receipt commit together. Validation/SQL
failure cannot create accepted rows. Repeated imports are idempotent; conflicting
hashes at an existing producer/epoch/sequence are refused. This is a conformance
catalog, not F17's authenticated continuity/enrollment anchor.

On reopening, only validated coordinator-generated `pending-*` staging links are
removed. Digest-named uncertain orphans remain, are revalidated before reuse and
count toward storage quota. Only missing objects consume additional quota, so a
failed registration can retry at a full budget. There is no pruning of accepted/
uncertain digest objects. Byte/count/message/entry/storage/free-disk and CPU/address-
space/time budgets are local policy with fixed qualification ceilings.

Remote clock claims remain in original bytes; the catalog adds local wall/monotonic/
session and full time-quality evidence. Unknown/degraded local time permits safe
opaque admission, but cannot invent healthy remote time, finality or cutoff proof.

## Effective authority and limits

The worker runs as namespace UID/GID 65534, without capabilities and with
no-new-privileges. Its filesystem is immutable; no host home/config/SSH/proc/run/
sys/dev paths, host sockets or inherited credentials are exposed. Only pipe FDs
0–2 reach bootstrap. This identity maps to the invoking host UID; it is not F21's
provisioned model service account and grants no additional host authority. Runtime/
code mounts are trusted operator-provisioned inputs and must contain no secrets.

Seccomp refuses networking (including Unix sockets), process/thread spawning,
exec, namespace/mount/identity changes, ptrace, device creation, io_uring and
kernel key/BPF/perf/userfaultfd/foreign-process access. Hard limits disable core/
file outputs and cap CPU/address space/open descriptors/processes. No GPU/device
or writable output mount exists. Monotonic supervision bounds pipe reads and
kills/reaps the process group on failure; PID namespace lifetime/die-with-parent
also constrain descendants and coordinator termination. Any missing control,
failed probe, decoder crash or timeout rejects work.

Before native loading every launch probes identity/no-new-privileges, hidden host
authority, exact environment keys, socket/process/filesystem denial, pipe-only
stdio, no inherited FDs and allocation beyond the hard address-space ceiling.
The profile uses rlimits plus complete process/thread denial. F21 model/GPU and
per-job cgroup qualification is not claimed. CI provisions mechanisms on an
ephemeral Ubuntu runner, including permitting namespaces; local host evidence
does not silently qualify another platform.

The operator, installed launcher/runtime, Linux kernel and storage remain trusted.
Host root, malicious code already running as the owning host UID, kernel exploits
and dishonest numerical/market results are outside these controls. Hashes/validator
success never prove parsing safety in another process or hardware power-loss
behavior. Future consumers must preserve this boundary after acceptance.

## Decision cross-checks

SOFA evidence reviewed: [process groups/inherited pipes](https://agents.stackoverflow.com/posts/a1b44506-6878-40c4-b8d5-175a85349aba),
[namespace setup restrictions](https://agents.stackoverflow.com/posts/23443c60-a56d-4355-8311-da8229e74774),
[filesystem versus restricted execution](https://agents.stackoverflow.com/posts/9e2a1da1-815e-442e-aaa9-2fbb3268a421).
Filesystem/TOCTOU/resource searches yielded no sufficient race recipe; source,
deterministic race/crash probes and Context7 `/python/cpython` and
`/containers/bubblewrap` documentation supplied verification. SOFA posts are not
authority; the connection was read-only and no public contributions were made.

Primary references: [Python descriptor operations](https://docs.python.org/3.12/library/os.html),
[subprocess](https://docs.python.org/3.12/library/subprocess.html),
[Bubblewrap policy/limitations](https://github.com/containers/bubblewrap),
[seccomp](https://man7.org/linux/man-pages/man2/seccomp.2.html),
[resource limits](https://man7.org/linux/man-pages/man2/getrlimit.2.html),
[memfd/seals](https://man7.org/linux/man-pages/man2/memfd_create.2.html).
