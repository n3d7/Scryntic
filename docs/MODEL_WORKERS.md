# Fixed model-worker CPU profile (F21)

The implementation is **pending privileged host qualification**. It must not be
described as a qualified production profile until all tests in
`tests/model_worker/test_host.py` pass on the declared deployment host. See
[F21_VALIDATION.md](F21_VALIDATION.md) for the current evidence and blockers.

`IsolatedFakeProvider` implements F20's `JobProvider` protocol. The coordinator
still owns model/policy admission, dataset verification, durable job state,
attempt fencing, cancellation/deadlines and immutable artifact acceptance.
There is no real model loader, preprocessing, GPU profile or financial authority.
The only executable provider is the already pinned F20 built-in fixture. Its
selected primitive inputs are copied into a private request, rather than exposing
the coordinator's dataset catalog, credentials or native decoding libraries.
The application/runtime mounts contain trusted installed code; no arbitrary
model, dataset path, shell command or unit properties can be supplied by a job.

## Fixed controls

The Linux x86-64 `f21-cpu-v1` launcher uses a systemd **system** transient service.
systemd 257+ is necessary for PrivatePIDs; actual controls, not a version string,
determine acceptance. The system manager must already be authorized to create
this fixed workload. The launcher never invokes sudo, opens a permission prompt,
installs services, grants polkit access or falls back to user services. In
particular, do not grant an application generic privileged systemd-run access.
Operator-owned launch authority and deployment provisioning remain trust/F27
concerns, not a generic elevated API added by F21.

| Boundary | Required configuration and effective preflight |
| --- | --- |
| Identity | A new DynamicUser per unit/attempt, host UID distinct from coordinator and root, matching GID, no unrelated supplementary groups |
| Namespaces | Separate user, mount, PID, network, IPC and UTS IDs; worker PID 1 sees only itself in proc |
| Filesystem | Empty generated root; selected CPython, application, system libraries, request and own cgroup bind read-only; private home/tmp/output mounts with nodev/nosuid/noexec |
| Network/sockets | PrivateNetwork and F16-derived seccomp denial of socket/socketpair/connect/bind/listen/accept, including AF_UNIX; no host sockets or agents mounted |
| Privilege/devices | NoNewPrivileges, zero inheritable/permitted/effective/bounding/ambient capabilities, PrivateDevices, denied device creation, namespaces, ptrace, exec, process/VM access, keyring and mount syscalls |
| Environment/FDs | Fixed HOME/PATH/LC_ALL; strip manager environment names and generated activation/credential variables, isolated Python `-I -S -B`, no inherited FD above stderr |
| Memory/process/CPU | cgroup v2 memory.max 256 MiB, memory.swap.max 0, pids.max 8, cpu.max 100000/100000; hard AS 256 MiB, CPU 5 s, NPROC 0, NOFILE 32, CORE 0; seccomp disallows fork/clone/threads |
| Output/time | FSIZE 64 KiB, output tmpfs 1 MiB, private tmp 16 MiB/home 1 MiB; unit RuntimeMaxSec 30 s, coordinator bounded pipes and wall time, KillMode=control-group and final SIGKILL |

The trusted bootstrap checks effective namespaces, status, rlimits, own cgroup,
mount permissions, actual tmpfs filesystem/byte quotas, environment and descriptors
(including open-but-unlinked files) before calculating a result. It
also attempts denied host-file/device reads, socket creation, input modification,
process creation and over-limit allocation on every job. Unsupported properties,
missing controllers or ineffective controls reject the workload. The coordinator
rechecks the bounded evidence and independently validates the F20 response.
Evidence is an observation from the trusted bootstrap, **not remote attestation**
or proof that a malicious worker's numerical output is correct.

## Result and lifecycle boundary

Worker-created files stay inside its private output tmpfs. `read_result` opens
only `result.json` relative to a pinned directory FD with O_NOFOLLOW/O_NONBLOCK,
requires a same-identity regular single-link file, bounds its size/read and
rejects inode metadata changes. The coordinator never follows worker paths or
imports a native/executable file. It accepts only bounded canonical primitive
JSON through pipes, checked against job, attempt, token, provider and request.

Cancellation shields and joins **all** cleanup, including subprocess creation,
unit stop, process-group kill and pipe EOF. A cleanup failure rejects acceptance.
No timeout, error text or diagnostic stream is promoted to a result. F20's
durable store remains authoritative after interruption/retry; no new job states
or duplicate-success path is introduced.

An abruptly killed coordinator cannot run its Python cleanup. The system unit
still has CPU/runtime limits and cannot publish an accepted result by itself.
Private unit mounts/identity are released by systemd; generated host staging may
remain after SIGKILL. Operators must confirm the unit is stopped before removing
stale `scryntic-model-*` staging directories. Immediate coordinator-death binding
and deployment-owned restart cleanup are not established by these tests.

## Trust and limitations

The TCB is the operator-owned launcher/installed runtime and standard library,
systemd manager/identity allocator, libseccomp, Linux namespaces/cgroups/rlimits,
filesystem mount/descriptor semantics and host MAC configuration. The selected
runtime/library mounts must contain no credentials and must be operator-reviewed;
read-only mounting does not establish supply-chain safety. A malicious operator,
host administrator or kernel/runtime compromise is outside this claim.

This CPU baseline deliberately denies subprocesses and threads. A real loader or
threaded/native runtime is not qualified by the fixture; F22 must meet/requalify
the effective CPU contract without silently broadening syscall/device authority.
No GPU device, driver, shared-memory behavior or exceptions have been qualified.
Resource limits bound this workload; they do not eliminate kernel exploits,
timing side channels or every host-wide contention effect. Do not claim absolute
containment or extrapolate one host's evidence to another kernel/runtime.

Primary references consulted through Context7 and upstream documentation:
[systemd execution controls](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml),
[resource controls](https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml),
[cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html),
[open and O_NOFOLLOW](https://man7.org/linux/man-pages/man2/open.2.html),
[asyncio subprocesses](https://docs.python.org/3.12/library/asyncio-subprocess.html),
[cancellation/shield](https://docs.python.org/3.12/library/asyncio-task.html).
The SOFA environment smoke-test post `29322753-da28-4dc9-b888-82e5d52bc58a`
was a second opinion on manager vs shell inheritance; upstream systemd docs and
source, not its trust score, determined the sanitization policy.
