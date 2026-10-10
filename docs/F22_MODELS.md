# F22 local model selection and qualification

F22 is not release-qualified yet. The implementation and portable checks are
available, including a quality-eligible real Bybit dataset and F19 baseline.
Mandatory F21/F22 host probes and actual TimesFM comparison remain outstanding.
See [F22_VALIDATION.md](F22_VALIDATION.md).

## Selection and provider boundary

Copy [the configuration example](../examples/local-model.toml). A closed TOML
`[model]` table selects `fake-persistence`, `fake-trend`, or `timesfm-2.5`.
Unknown fields, imports, shell commands, unit configuration and incomplete real
model enrollment fail closed. `python -m scryntic.models.provision list` lists the
owned registrations. Configuration is read explicitly; ambient variables do not
select executable code. Existing supported providers require a configuration
change, without application, dataset, replay, job or evaluator changes.

`models.provider.select_provider` supplies the existing F20 `JobProvider`.
Definitions bind adapter, immutable admission metadata, artifact/runtime
manifest and fixed CPU profile. A compatible future model needs its focused
adapter, reviewed definition/pins and, when necessary, separately qualified
runtime/profile. There is no dynamic plugin import or third-party marketplace.
Shared services contain no TimesFM imports or assumptions. Vendor imports occur
only after effective isolation checks inside the worker. F20 closed job/result
schemas, authoritative inputs, admission, deadlines, fencing, cancellation and
immutable result acceptance remain in charge.

## Reviewed TimesFM identity

The official [Google model card](https://huggingface.co/google/timesfm-2.5-200m-pytorch/tree/1d952420fba87f3c6dee4f240de0f1a0fbc790e3)
and [upstream project](https://github.com/google-research/timesfm) were checked on
2026-10-06 and rechecked on 2026-10-09. Model/package license: Apache-2.0.
The newer TimesFM 3 weights have different terms and are not selected here.
This does not replace the licenses
of runtime dependencies (including PyTorch/numpy BSD terms); their installed
license metadata is covered by the runtime inventory and lock/SBOM.

Revision: `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| model.safetensors | 925181104 | `2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8` |
| config.json | 475 | `cd3315b760d5cc7e278d7afdf41b897031ced888fc6c115bd9b3ac0ea2c47408` |
| README.md | 2496 | `4c3f16633a3d8b9324904bf004429aced2ee009b84e64effb59a902520cc7f2e` |

The separate `runtimes/forecast_cpu` environment pins CPython 3.12.14, TimesFM
3.0.2, torch 2.14.0+cpu from the official CPU index, numpy 2.5.3, safetensors 0.8.0
and huggingface-hub 2.1.1, with 25 locked dependencies. No ML dependency is added
to coordinator/collector profiles. Exact uv CLI: 0.12.18. Runtime lock SHA-256:
`4dd42f738e5ea4ec00240acb593d0eeee7e5b0f85ed80b585de88d8fdca2946f`.
TimesFM wheel SHA-256:
`cfa3d22ddc3f019f0d9d95f19055428c4a4713526dafd7e5d1a7769782e7613f`.
torch CPU wheel SHA-256:
`a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260`.

Provision outside the worker, from a trusted clean locked installation:

```sh
uv sync --project runtimes/forecast_cpu --locked --no-default-groups --link-mode copy
```

Download the three artifacts from the fixed revision above into a private
absolute directory. For example, with an operator-selected private `DOWNLOADS`:

```sh
curl --fail --location --proto '=https' --proto-redir '=https' \
  'https://huggingface.co/google/timesfm-2.5-200m-pytorch/resolve/1d952420fba87f3c6dee4f240de0f1a0fbc790e3/model.safetensors' \
  --output "$DOWNLOADS/model.safetensors"
# Repeat the same fixed-revision URL for config.json and README.md.
.venv/bin/python -m scryntic.models.provision import-artifacts \
  --source "$DOWNLOADS" --destination "$ARTIFACTS"
.venv/bin/python -m scryntic.models.provision inventory \
  --runtime "$RUNTIME" --lock "$PWD/runtimes/forecast_cpu/uv.lock"
.venv/bin/python -m scryntic.models.provision verify \
  --artifacts "$ARTIFACTS" --runtime "$RUNTIME" --runtime-sha256 "$APPROVED_SHA"
```

`ARTIFACTS` must be a new private directory; `RUNTIME` is the absolute dedicated
venv path. Enrollment writes a new `inventory.json` and prints its hash. Review
and put that hash in TOML as `runtime_sha256`; never re-enroll drift automatically.
The inventory checks the exact full installed dependency closure without
importing vendor code, then records every admitted file's size/hash. The trusted
operator, hash-verifying uv installer and clean provisioning host remain part of
the supply-chain trust boundary. Installed metadata can differ between clean
hosts, so record each explicit enrollment hash; byte-identical venv recreation
across hosts is not claimed.

Each attempt verifies and copies all admitted bytes into private staging. Source
ancestors/files are opened without symlink traversal; size/hash/stat identity
are checked during copy. Later source replacement cannot change the worker's
copy. Model/runtime mounts are read-only. No Hub APIs, pickle loader, remote code,
runtime downloads, user site, `.pth` hooks or ambient credentials are used.
The adapter calls the inspected `load_checkpoint` safetensors path directly,
with `torch_compile=False`, instead of `from_pretrained`.

## CPU profile and preprocessing

`f22-cpu-v1` is a separate candidate profile, not an assertion that F21's
synthetic profile qualifies PyTorch. It retains per-job DynamicUser, required
namespaces, immutable mounts, empty capabilities, no-new-privileges, denied
sockets/network/exec/fork, exact environment and safe bounded F21 result import.
Candidate bounds: memory 4 GiB, no swap, address space 8 GiB, CPU 90 seconds,
wall 180 seconds, 8 tasks, 64 descriptors and cgroup CPU quota 100%.
These are qualification bounds, not measured successful TimesFM resource use.

Candidate scratch mounts are `/worker-home` (1 MiB, HOME), `/worker-tmp`
(16 MiB, TMPDIR) and `/output` (1 MiB), each private tmpfs with required
`rw,nodev,nosuid,noexec` flags. `/home` remains protected; `/tmp` and `/var/tmp`
are inaccessible and preflight requires writes there to be denied. These paths
avoid the systemd ProtectHome/implicit PrivateTmp conflicts documented in
[F22_VALIDATION.md](F22_VALIDATION.md). The relocated mounts still need actual
host qualification; declared settings are insufficient evidence.

Native CPU libraries need pthreads. The additional seccomp mode allows only the
reviewed x86-64 glibc pthread `clone` flags `0x3d0f00`; other clone flags are denied.
`clone3` returns ENOSYS for glibc's documented fallback. Fork/vfork/exec/network
remain denied; cgroup TasksMax and RLIMIT_NPROC bound threads. The synthetic F16/
F21 syscall profile still denies all clone calls. Preflight tests actual thread
exhaustion and checks thread policy, host-file/socket denial, immutable inputs,
model/runtime write denial and resource controls before adapter code.

The common context contains 2..512 finite, contiguous, observable close values,
converted deterministically to bounded floats (absolute close <= 1e20).
It uses F19 `feature_result` at the decision clock, stops at gaps/excluded or
unobservable history, never imputes and never passes labels. F20's last-two-close
verification remains unchanged. TimesFM converts to float32, uses window-local
normalization, eager CPU inference, one thread, explicit seed and deterministic
PyTorch algorithms; continuous quantile head/flip invariance are disabled.
The runtime forecast configuration uses context 512 and rounded horizon 128;
only the requested 1..24 point predictions cross into primitive F20 contracts.
Point timestamps remain candle starts; evaluation scores the final requested
point against F19's corresponding close label/end time.

## Comparison and reproducibility

`scripts.prepare_f22_bybit` captures a bounded public BTCUSDT spot 1-minute
history through F15 durable recovery and actual coverage evidence, publishes it,
imports via F16, builds/pins F18 provenance and evaluates the F19 baseline.
Default quality/gap exclusions remain strict. Historical reconstruction is
explicitly identified; this is not an as-observed availability claim.

`scripts.check_f22_model` prepares the same verified F18/F19 reader and selects
the configured provider. It compares the first bounded eligible contexts per
series/partition against persistence using F19's exact label purging and metrics,
on identical cases. Full context, replay configuration, dataset, seed and model
review and a per-invocation qualification ID bind request IDs. Two distinct jobs
per case check repeated execution; rerunning evaluation executes fresh jobs
rather than reusing cached results or changing an existing job deadline;
successful jobs create immutable F20 forecast artifacts. Changing provider
configuration does not collide with jobs of a different model/runtime review.
Reports retain pins/provenance, exclusions, common metrics, forecasts, artifact
references, model/runtime hashes, code/environment, effective controls and
resource measurements. Empty eligible input refuses launch.

The supplied model is pretrained and is not fitted on these splits. Unknown
pretraining overlap prevents a model-training holdout claim. Neither superior
quality nor cross-hardware/version bitwise reproducibility is assumed; see
[PyTorch reproducibility guidance](https://docs.pytorch.org/docs/2.14/notes/randomness.html).

## Trust and limitations

Kernel, systemd/cgroups, user/mount/PID/network namespaces, seccomp/libseccomp,
glibc, installed CPython/shared libraries, coordinator/bootstrap and reviewed
runtime provisioning are trusted. Effective-control reports come from trusted
preflight and are revalidated by the coordinator; they are not cryptographic
attestation against a compromised kernel/bootstrap or a root administrator.
Threads share worker address space. Native model dependencies and allowed kernel
syscalls retain exploitation/side-channel risk. Read-only code/library/model
mounts are deliberately visible. Cgroup/rlimit controls bound worker resources,
not every host-wide contention or snapshot disk/I/O cost. Each attempt copies
about 1.76 GB; disk admission/performance beyond the existing job quota is not
claimed. Abrupt coordinator death relies on the fixed unit's wall-time bound.

No GPU profile is introduced; CPU-only torch is enforced. GPU device/driver
exceptions remain unqualified. No financial or exchange execution authority,
exchange credentials, arbitrary launcher or user unit/shell configuration is
introduced. Missing controls fail closed; an unavailable host is not qualified.

Primary behavior references used alongside Context7: the inspected pinned
TimesFM wheel/source; [glibc pthread implementation](https://sourceware.org/git/?p=glibc.git;a=blob;f=nptl/pthread_create.c;hb=HEAD);
[libseccomp rule documentation](https://libseccomp.readthedocs.io/en/latest/man/man3/seccomp_rule_add_exact.3/);
[systemd resource controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
and [execution sandboxing](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).
