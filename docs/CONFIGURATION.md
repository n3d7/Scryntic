# Configuration and credential boundary (F04)

`scryntic.configuration` is an outer, stdlib-only boundary. Domain/application
contracts do not import it. It performs no networking, process startup, directory
creation, credential discovery, environment-secret lookup or dynamic backend loading.
F27 owns systemd deployment and hardening; this task supplies neither units nor a
systemd credential implementation.

## Loading and precedence

The trusted composition layer selects an `Installation`, then calls
`load_configuration`. It must finish validation before starting any network-capable
component. Precedence is built-in defaults → one `config.toml` in the approved
configuration directory → explicit non-secret overrides. The only overrides are
`profile` and `log_level`; environment variables select workstation XDG locations,
not settings or secrets. Every supplied layer is validated, so a valid override
cannot hide an invalid file value. Missing files fail explicitly; an empty file
selects defaults. Input is bounded to 64 KiB.

```toml
profile = "collector"
log_level = "info"

[capabilities]
public_collection = true
telegram_reports = false
```

Profiles are `collector` and `workstation` (default). The collector defaults to
`public_collection`; the workstation defaults to `local_analysis`. Collector-only
optional capability: `telegram_reports`. Workstation-only optional capability:
`synchronization`. Booleans explicitly enable/disable capabilities. These are
configuration switches for later components, not implementations of them. Supported
log levels are `debug`, `info`, `warning`, `error`. Unknown keys, wrong types,
unsupported backends, duplicate definitions and incompatible enabled capabilities
fail. There are no exchange credential fields or financial capabilities.

Optional references use `[credentials.telegram]` or `[credentials.transfer]`, each
with `backend = "file"` and `name = "bot-token"` or another approved leaf name.
Names contain 1–64 ASCII letters, digits, underscores or hyphens, starting with a
letter or digit; they are identifiers, never secret values or arbitrary paths.
A reference may be present while its capability is disabled; its source is then
neither checked nor opened. Enabled credential-bearing capabilities require a
reference. Public collection requires and resolves no credential.

## Approved Linux paths

| Purpose | Service | Workstation |
|---|---|---|
| Configuration | `/etc/scryntic`, root-owned, readable/traversable by service, never service-writable | `$XDG_CONFIG_HOME/scryntic`, fallback account home + `.config/scryntic` |
| State | `/var/lib/scryntic`, dedicated service UID, `0700` | `$XDG_STATE_HOME/scryntic`, fallback account home + `.local/state/scryntic`, current UID, `0700` |
| Runtime | `/run/scryntic`, dedicated service UID, `0700` | `$XDG_RUNTIME_DIR/scryntic`, fallback state directory + `runtime`, current UID, `0700` |
| File credentials | `/etc/scryntic/credentials`, credential-owning UID, `0700` | configuration directory + `credentials`, current UID, `0700` |

F04 uses root as the operator owner for service configuration. Its directory need
not be `0700`: for example `0755`/`0750` can work if the service can traverse/read.
Configuration files must be regular, single-link, owned by the configuration owner,
with no special bits or group/other write permissions. The service UID must be
non-root and different from the configuration owner. State/runtime checks require
the current effective UID to match the expected owner. Workstation private Scryntic
directories, including configuration, are `0700`; no privileged workstation paths
are invented. Fallback home comes from the account database, not `$HOME`.

Operators provision directories; F04 never repairs permissions or creates them.
Empty XDG values use the fallback; relative paths and `..` components fail.
Ancestors must belong to root or the expected owner and be protected from other
writers. A root-owned sticky shared ancestor such as `/tmp` is permitted because
it protects the following owned directory from replacement by other UIDs; private
final roots still require `0700`. Every component is opened with directory file
descriptors and `O_NOFOLLOW`. No symlinks are followed or resolved away.

Credential files must be regular, single-link, owned by the credential-owning UID,
and exactly `0400` or `0600`. Group/other permissions, special bits, executable
files, hardlinks and special files fail. Reads are bounded to 64 KiB; empty secrets
fail. Descriptor-relative opens and `fstat` validate the object actually read;
metadata changes during reading fail. This avoids check-then-reopen substitution.
Permissions do not defend against root or malicious code already running as the
credential owner. F27 will assign separate adapter identities and deployment roots;
`SecretReference(capability, backend, name)` remains independent of those paths.

## Secret ownership and diagnostics

Only an enabled owning adapter constructs `FileCredentials(installation, config,
capability)`. It binds one validated reference and retains neither the whole
configuration nor an arbitrary-name lookup interface. `open()` accepts no name or
path and returns a context-managed `Secret`. The adapter accesses its readonly
byte view only while needed. The bot receives only its token; the transfer client
receives only its SSH credential. No default SSH agent or exchange resolver exists.
Later backends can implement the same reference semantics without changing domain
or application contracts; unknown backends fail closed today.

Ordinary `Configuration` values contain immutable settings and references, never
resolved bytes. `diagnostics()` allowlists profile, log level and enabled capability
names. Boundary errors expose fixed codes without input, paths, underlying OS/TOML
error text or chained exceptions. Secret wrappers have redacted `repr`/`str`, reject
pickling and clear their owned buffer on context exit, including exceptional exit.
Python/native libraries can retain copies: this is not a memory-erasure guarantee
or an in-process security sandbox. Trusted adapters must not dump byte views,
locals, parser documents, process arguments or exception locals. Models and generic
application services must never receive credential handles.

## Validation

`python -m pytest tests/configuration` exercises precedence, profile/capability
validation, public zero-resolution behavior, missing/unsafe files, owner/mode
checks, protected paths, symlinks, hardlinks, bounded reads, metadata changes and
synthetic sentinel leakage through output/errors/logging/serialization. POSIX policy
unit cases cover service owners/modes without privileged host installation.
Run filesystem tests against real Linux UID metadata; a namespace remapping root
to another UID is correctly rejected. `bash scripts/check.sh` also runs existing
import/type fences, packaging/profile integrity and dependency auditing.
