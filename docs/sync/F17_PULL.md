# F17 authenticated resumable pull

F17 is a workstation Python API. Application scheduling and user orchestration
belong to F24; this component does not provide an enrollment or pull CLI.
See [ARCHITECTURE.md](../../ARCHITECTURE.md)
for the trust boundary.

## Prepare the endpoint and workstation

Use a dedicated server account authenticated by one workstation public key.
Enforce a published-artifact-only filesystem jail and read-only SFTP permissions
on the server, including metadata changes, rename, removal and link creation.
Disable shell/command execution, PTY, TCP/X11 forwarding and agent forwarding.
The account must not expose live SQLite files, credentials or producer scratch
space. Publish immutable manifests and content-addressed objects, and retain
manifest history independently of raw-object retention. The Python adapter does
not configure these server controls; complete deployment qualification and
automation belong to F27.

Obtain the numeric IP, port, username, producer, epoch and exact Ed25519 public
host key through an independently authenticated operator channel. The pin has
the form `ssh-ed25519 BASE64`, without a comment. Do not obtain the initial pin
from an unverified first connection. The enrolled remote root is the archive
directory containing `manifests/`, `objects/` and optional `head.json`; it must
be an absolute non-root path inside the jail, for example `/archive`.

Use the pinned workstation `analysis` dependency group. Prepare the workstation
installation directories before opening the API: they must be owned by the
workstation user, private and free of symlink substitution. The defaults from
`Installation.workstation()` place configuration under
`~/.config/scryntic`, state under `~/.local/state/scryntic`, and credentials
under `~/.config/scryntic/credentials`; supported XDG overrides may change these
paths. Existing configuration remains authoritative.

Enable synchronization in `config.toml`:

```toml
profile = "workstation"

[capabilities]
synchronization = true

[credentials.transfer]
backend = "file"
name = "pull-key"
```

Provision the private key as `installation.credential_dir / "pull-key"`.
The name is exact: no suffix is appended. The credentials directory must be
private (`0700`); the key must be a regular, user-owned file with one link and
mode `0400` or `0600`, at most 65,536 bytes. Provide an importable unencrypted
private key; the adapter has no passphrase/password prompt. It owns
`FileCredentials(installation, configuration, Capability.SYNCHRONIZATION)` and
imports the key from its short-lived protected buffer. The client ignores user
SSH configuration and key discovery, disables the SSH agent and alternate
authentication methods, and opens only SFTP.

## Explicit enrollment and pull

Choose genesis deliberately, or supply the exact canonical checkpoint manifest
received through the trusted operator channel. A checkpoint acknowledges that
earlier coverage is unavailable; it does not assert that unavailable objects
passed F16 validation. Repeating an identical enrollment is idempotent;
conflicting pins, endpoint data or bootstrap boundaries are rejected.

This example defines application wrappers around the existing API. Supply the
verified public pin and any checkpoint bytes explicitly before enrollment.

```python
from scryntic.clock.monitor import ClockMonitor
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import Enrollment, PullLimits, PullResult
from scryntic.sync.pull import pull
from scryntic.sync.sftp import SFTPRemote

installation = Installation.workstation()
configuration = load_configuration(installation)
limits = PullLimits()


def enroll(verified_host_key: str, checkpoint: bytes | None = None) -> None:
    endpoint = Enrollment(
        name="collector-a",
        host="192.0.2.10",  # Replace with the enrolled numeric IP.
        port=22,
        username="scryntic-reader",
        remote_root="/archive",
        host_key=verified_host_key,
        producer="collector-a",
        epoch="epoch-a",
    )
    with PullCatalog(installation, pull_limits=limits) as catalog:
        catalog.enroll(endpoint, checkpoint=checkpoint)  # None selects genesis.


async def synchronize(clock: ClockMonitor) -> PullResult:
    with PullCatalog(installation, pull_limits=limits) as catalog:
        endpoint = catalog.enrollment("collector-a")
        async with SFTPRemote(endpoint, installation, configuration, limits) as remote:
            return await pull(catalog, endpoint.name, remote, clock.sample(), limits)
```

The application supplies an F15 `ClockMonitor` with its local `ClockReader` and
`SynchronizationStatus` adapters, then obtains the `ClockSample` using
`sample()`. Do not fabricate healthy clock evidence from server timestamps or
plain wall-clock reads. The sample records local receipt context; server times
remain claims from another host. Keep the catalog on its owning thread and
serialize access to the workstation installation.

## Progress, limits and failures

Defaults bound each network operation to 10 seconds and a pull to 120 seconds,
with 64 KiB reads, 10,000 entries per listing, 10,000 discovered manifests,
16 newly processed manifests, 64 MiB of discovery bytes, and 128 MiB each for
transfer, staging and trust backup bytes. `PullLimits` enforces finite ceilings;
use the same limits for the catalog, adapter and pull. The pinned AsyncSSH
2.24.0 adapter also caps incoming SFTP frames and SSH receive windows at 256 KiB.
Its narrowly used `asyncssh.sftp.start_sftp_client` factory is an internal
compatibility dependency whose signature and reader behavior must be rechecked
on upgrade.

Private `state_dir/pull-staging` files resume per digest from a verified local
length. Invalid partials can restart that object. Exact final size and SHA-256
are mandatory, and cached objects are checked again before reuse. F16 validates
each new acceptance inside its restricted worker; native decoders never run in
the credential-bearing transport/coordinator. Its synchronous bounded worker
wait can exceed the network wall deadline for one response. After the worker
returns, a deadline check in the registration transaction rejects an already
late response before advancing the accepted anchor.

Discovery follows publication sequence, including late corrections, and
compares every retained accepted manifest across enrolled epochs. It requires
consecutive predecessors and checkpoint continuity. Rewrites, truncation, gaps,
unexpected epochs and new history in a retired epoch fail closed. A failed pull
does not reset anchors; already committed acceptances remain durable. Retry
the pull after resolving the failure, rather than deleting state.

`PullResult.complete` indicates whether all discovered pending manifests fit
this pull's processing allowance. Repeat when it is false. Freshness is
`current` when the optional head matches discovery, `unknown` when absent, and
`degraded` for a stale or malformed hint. Degraded hints trigger a bounded
discovery retry and never authorize bypassing history checks. Even a current
hint cannot prove that a compromised producer published everything it knows.

SFTP v3 `lstat` checks reject visible symlink/special-file paths but cannot
eliminate a hostile peer changing a pathname between checks and open. The
enrolled host, generated artifact namespace, final hashes and manifest checks,
and server-side jail are the applicable boundaries. These checks establish
conformance and continuity, not market truth after producer compromise.

## Epoch changes and independent trust backups

Finish synchronizing the old epoch before the operator authorizes a new one:

```python
with PullCatalog(installation, pull_limits=limits) as catalog:
    catalog.authorize_epoch("collector-a", "epoch-b", checkpoint=None)
```

Use `checkpoint=verified_manifest_bytes` instead when explicitly accepting an
unavailable-history boundary. Authorization preserves old anchors/manifests and
checks known input continuity during acceptance. The server account cannot
authorize epochs. Do not repair an integrity incident by deleting the catalog,
resetting sequences, or silently enrolling a replacement history.

`catalog.backup()` returns bounded canonical bytes containing endpoint pins,
bootstrap/accepted anchors and retained manifests. Persist those bytes outside
the server's retention authority, in a protected user-owned regular file:
private parent directory, mode `0600` or `0400`, one link, no-follow access,
bounded reads and an atomic durable write with file/directory fsync and
readback. Maintain independently authenticated copies or digests through a
safe out-of-band channel; a copy kept only beside the server is insufficient.

Restore explicitly with `catalog.restore(verified_backup_bytes)`. Restoration
is transactional and idempotent, rejects conflicting pins/history and rollback
of existing anchors, and restores trust only. Backups exclude credentials,
object contents and import rows; restore does not mark data as F16-validated.
Re-provision the private key separately, then pull available objects for normal
validation. Preserve the installation's protected data/catalog storage
separately when needed. A deliberately supplied stale trust backup on an empty
workstation cannot be distinguished cryptographically from its historical state.
