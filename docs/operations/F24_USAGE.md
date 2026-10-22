# CLI and workstation terminal UI (F24)

The `scryntic` command and optional terminal UI use the same typed requests,
workflow metadata, application services and fixed diagnostics. Help is available
without an installed model runtime, SSH credential or terminal UI dependency.
Work begins only after an explicit command or **Run workflow** action.

## Installation and launch

Use the project's pinned Python and uv versions. For a source checkout:

```sh
uv sync --locked --group analysis
.venv/bin/scryntic --help
```

The analysis dependency group supplies the Parquet and authenticated pull
adapters. A base installation can inspect configuration and source/provider
metadata; operations requiring an absent adapter report a fixed unavailable
error. The collector dependency group supplies Parquet without AsyncSSH.

Textual is a separate optional extra:

```sh
uv sync --locked --group analysis --extra tui
.venv/bin/scryntic tui
```

For an installed wheel, select its `[tui]` extra when installing it. Parquet
and synchronization workflows also need the locked analysis adapters; the TUI
extra supplies Textual itself. A headless installation does not import Textual
during ordinary CLI commands.

Provision the protected installation directories and `config.toml` before
running stateful work; the application does not repair directory permissions.
Default workstation paths and credential provisioning are described in
[CONFIGURATION.md](CONFIGURATION.md). The global `--home` option selects an
explicit workstation home for both CLI and TUI. It does not accept secret values
or change the credential ownership rules.

The global `--installation workstation|service` selects the installation mode;
put global options before the command. New workflow commands emit bounded JSON
by default, and `--json` is accepted globally or on a workflow command. For example:

```sh
scryntic --home /home/analyst config validate
scryntic sources inspect --profile fixture
scryntic dataset inspect --manifest-sha256 <accepted-dataset-hash> --limit 20
scryntic --installation service status
```

## Workflows

The shared registry supplies each operation's fields, defaults, choices and
validation to both interfaces. Use command help for the current CLI spelling and
the TUI's generated form for the same parameters. Required fields carry `*`.

| Operation | Purpose and required context |
|---|---|
| `config.validate` | Validate the approved configuration and report allowlisted settings. |
| `sources.list` | Inspect registered public sources. |
| `sources.inspect` | Inspect one registered source's capabilities. |
| `collect` | Run explicitly selected, bounded collection. |
| `backfill` | Request bounded history from a registered source supporting it. |
| `status` | Inspect the local installation snapshot. |
| `sync.status` | Inspect one enrolled endpoint and its durable accepted anchors offline. |
| `sync.enroll` | Explicitly enroll independently verified public endpoint metadata and a deliberate genesis/checkpoint boundary; the TUI requires confirmation. |
| `sync.pull` | Pull from an existing enrolled endpoint through the authenticated read-only adapter. |
| `data.inspect` | Inspect an accepted input identified by its exact manifest SHA-256. |
| `dataset.build` | Build an immutable dataset from distinct accepted hashes and explicit revision, timing, coverage and time-quality policies. |
| `dataset.inspect` | Read a bounded row page from an exact dataset manifest hash. |
| `dataset.export` | Export the exact manifest and Parquet bytes to a new protected destination leaf. |
| `replay` | Evaluate an exact dataset with explicit train/validation/test time boundaries and decision-time policies. |
| `providers.list` | Inspect the registered provider metadata; no arbitrary provider module is loaded. |
| `analyze` | Submit and execute a durable forecast job for an immutable dataset. |
| `jobs.submit` | Submit a bounded job with an explicit identifier, provider, replay range and deadline. |
| `jobs.list` | Inspect a bounded page of durable jobs. |
| `jobs.status` | Inspect one durable job's state. |
| `jobs.run` | Run a queued job. |
| `jobs.resume` | Resume an eligible interrupted job against its immutable context. |
| `jobs.cancel` | Permanently cancel one durable job; the TUI requires confirmation. |
| `jobs.result` | Inspect a successfully accepted artifact; incomplete jobs do not expose a result as accepted. |

Dataset and job commands identify immutable artifacts by hash, rather than a
mutable latest-file alias. `dataset.inspect` supports `offset` and `limit`.
Historical reconstruction and `as-observed` are explicit choices; the latter
requires an independently captured cutoff with full clock-quality evidence.
Replay boundaries are exact UTC nanoseconds and must increase strictly.

Collection reports distinguish live receipt from historical repair. `received`
counts messages delivered through the live source intake; `repaired_candles`
counts recovered history. A successful backfill can therefore have zero live
receipts while committing, normalizing and publishing historical candles. Use
the explicit coverage, fault, shutdown and completion fields together; the
receipt counter alone does not establish continuity or durable completion.

Execution of even a fake forecast provider requires the reviewed CPU worker's
existing fixed system-manager launch authorization, described in
[MODEL_WORKERS.md](MODEL_WORKERS.md). Reading system-manager status does not
establish launch permission. A denied launch remains a failed durable job with
no accepted result; the CLI does not elevate privileges or relax worker controls.

Exports use a new directory below the installation's protected
`state_dir/exports`. A destination is a leaf identifier, not an arbitrary path.
Successful bundles include a durable `complete.json` with artifact hashes.
An incomplete bundle must not be treated as committed, and existing destinations
are not overwritten. Model descriptors and captured input policy documents are
protected files in the approved configuration directory.

## Terminal workspace

The dashboard states local readiness without starting a remote connection.
Search workflows by title, group or operation name, then press Enter to open the
form. Choice fields expose only the shared allowed values. Tab moves between
controls. Compact terminals can scroll the main workspace; long forms also
scroll independently.

| Shortcut | Action |
|---|---|
| `Ctrl+R` or `F5` | Validate the form and run the selected workflow. |
| `Ctrl+F` | Focus workflow search. |
| `Ctrl+K` | Request cancellation and wait for owned work to settle. |
| `?` | Open keyboard help outside a text input. |
| `Ctrl+Q` or `Ctrl+C` | Settle work, close owned resources, then quit. |
| Enter on a result row | Open its full public value. |
| Escape in a modal | Go back or close the modal. |

The result table displays at most 200 rows with compact cells. Its filter searches
the retained public values; selecting a row opens the complete value in a
scrollable detail view. JSON-looking strings and markup are displayed literally.
The CLI's bounded JSON envelope is the machine-readable interface.

While work runs, forms and Run are disabled and progress is indeterminate: the UI
does not invent a completion percentage. Navigation search remains responsive,
including during cancellation cleanup. Changing the selected form waits for the
current operation to settle. A result marked incomplete remains visibly
incomplete.

## Cancellation, status and failures

Cancellation belongs to the shared service. The UI retains its execution and
cancellation tasks and waits for cleanup; cancelling a presentation task is not
proof that a worker or subprocess stopped. Quit uses the same settlement path.
Durable job cancellation and cancellation of the current interface operation are
separate actions.

Status is an offline local snapshot. F24 does not claim a live daemon socket,
remote health, or start/stop/restart control of the F25 daemon. An offline sync
status cannot establish current remote freshness. Opening a catalog can take its
exclusive storage lock and perform existing local recovery; a competing owner
is reported rather than bypassed.

Authenticated synchronization requires the enabled workstation capability and
the adapter-owned file credential described in [F17_PULL.md](../data/sync/F17_PULL.md).
Public enrollment identity and a verified host-key pin are not private-key
bytes. Pull uses the generated manifest/object namespace below the fixed enrolled
archive root; there is no generic SSH command, remote-path read, default agent,
or credential argument. Retry an interrupted pull against preserved anchors.
Deletion of trust state is not a recovery operation. Retention CLI work belongs
to F29.

Errors cross the interface as fixed codes and messages. Adapter exception text,
tracebacks, credentials and parser internals are not rendered. CLI exit codes:

| Code | Meaning |
|---|---|
| `0` | Complete successful operation. |
| `2` | Invalid request or command arguments. |
| `3` | Configuration or credential policy requires correction. |
| `4` | Unsupported capability. |
| `5` | Unavailable service/artifact or a resource owned elsewhere. |
| `6` | Operation failed; durable checkpoints are preserved. |
| `7` | Incomplete operation; inspect durable state before continuing. |
| `130` | Cancellation after owned work settled. |

An incomplete execution (`analyze`, `jobs run` or `jobs resume`) returns its
durable job in the stdout result envelope with `ok: true`, `complete: false` and
exit code `7`. Requesting `jobs result` before an accepted successful artifact
exists returns the stderr error envelope with `ok: false`,
`error.code: operation_incomplete` and the same exit code. Consumers must read
the envelope and its channel as well as the exit code.

## Installed application checks

`scripts/check_package.py` builds the wheel and source distribution, verifies
their package contents, and tests clean base, collector, analysis and TUI
installations outside the checkout. Its CLI verifier also calls typed
application services directly and checks that this does not import the CLI.
The TUI profile opens all 23 generated workflow forms, representative real-service
forms, and the actual installed console script on a terminal, including
keyboard navigation, help and quit.

The analysis smoke uses explicitly synthetic candles and a local authenticated,
read-only SFTP server. Portable checks exercise real admission, recovery and
failure handling with an explicitly denied external launch. This denial is
injected only by the verification subprocess; the installed application has no
test switch or fallback worker. It must retain a failed durable job, sanitized
diagnostics and no accepted artifact.

Successful isolated execution remains a separate mandatory delivery check.
On an existing authorized and provisioned F21/F22 worker host, run:

```sh
SCRYNTIC_F24_HOST=1 .venv/bin/python -m pytest -v \
  tests/interface/test_install_smoke.py::test_analysis_console_uses_real_pull_data_replay_and_model_worker
SCRYNTIC_F24_HOST=1 bash scripts/check.sh
```

The flag selects the real positive test and installed-wheel `analysis-host`
verification. It grants no permissions and bypasses no isolation controls.
Both require a successful isolated worker and accepted artifact; exit 7 fails
qualification. Without the flag the portable gate explicitly reports real-host
execution as unqualified. Successful pull, dataset, replay or denied-worker
checks alone do not qualify model execution.

Collection runs only as a non-root installation owner. Run the combined flag
example only on a host where that same explicitly authorized operator can launch
required system-manager workers. Root is not a substitute for the collector
owner. On the dedicated lab the ordinary-user portable/wheel/TUI checks and
explicit privileged model checks are qualified separately: the unchanged
`scripts/verify_cli_install.py` function
`verify_analysis(installed_console, private_scratch, require_worker_success=True)`
exercises the installed analysis portion without its unprivileged collector
prelude. Record this split rather than claiming the combined role passed.

The currently qualified F21/F22 worker execution environment uses `umask 022`.
Keep protected configuration directories/leaves at their required 0700/0600;
do not loosen credential permissions. Worker launch under a restrictive 077
umask remains a documented pre-existing staging limitation. An execution failure
still preserves failed durable state and accepts no artifact.

Validation status, genuine results and remaining blockers are recorded in
[F24 validation](../validation/F24_VALIDATION.md).
