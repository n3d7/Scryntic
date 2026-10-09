"""Effective OS controls checked by trusted bootstrap before worker code."""

import ctypes
import errno
import os
import resource
import socket
import tempfile
import threading
from pathlib import Path
from typing import Any

from scryntic.model_worker.profile import (
    CONTROL_PATH,
    NAMESPACES,
    OUTPUT_BYTES,
    REQUEST_PATH,
    SYNTHETIC_CPU,
    TMPFS_BYTES,
    WRITABLE_MOUNTS,
    CPUProfile,
    IsolationError,
)


def namespace_ids() -> dict[str, str]:
    return {name: os.readlink(f"/proc/self/ns/{name}") for name in NAMESPACES}


def extra_descriptors() -> list[int]:
    descriptors = []
    for name in os.listdir("/proc/self/fd"):
        if not name.isdigit() or int(name) <= 2:
            continue
        number = int(name)
        try:
            os.fstat(number)  # Includes open-but-unlinked files, not just pathnames.
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            descriptors.append(number)
    return sorted(descriptors)


def resource_limits() -> dict[str, list[int]]:
    return {
        name: list(resource.getrlimit(getattr(resource, "RLIMIT_" + name)))
        for name in ("AS", "CPU", "CORE", "FSIZE", "NOFILE", "NPROC")
    }


def expected_limits(profile: CPUProfile = SYNTHETIC_CPU) -> dict[str, list[int]]:
    profile.require_owned()
    return {
        name: [value, value]
        for name, value in (
            ("AS", profile.address_space),
            ("CPU", profile.cpu_seconds),
            ("CORE", 0),
            ("FSIZE", OUTPUT_BYTES),
            ("NOFILE", profile.nofile),
            ("NPROC", profile.nproc),
        )
    }


def denied(operation: Any) -> None:
    try:
        operation()
    except OSError as error:
        if error.errno in (errno.EPERM, errno.EACCES, errno.EROFS, errno.ENOENT):
            return
        raise IsolationError("Unexpected denial") from None
    raise IsolationError("Required boundary is ineffective")


def _read_file(path: str) -> None:
    with open(path, "rb") as stream:
        stream.read(1)


def _write_input() -> None:
    with open(REQUEST_PATH, "ab") as stream:
        stream.write(b"tamper")


def _fork() -> None:
    child = os.fork()
    if child == 0:
        os._exit(1)
    os.waitpid(child, 0)


def _stage(stage: list[int] | None, code: int) -> None:
    if stage is not None:
        stage[0] = code


def probe_boundaries(
    host_canary: str,
    profile: CPUProfile = SYNTHETIC_CPU,
    *,
    stage: list[int] | None = None,
) -> None:
    _stage(stage, 96)
    for path in (
        host_canary,
        "/etc/shadow",
        "/root/.ssh/id_ed25519",
        "/run/docker.sock",
        "/run/dbus/system_bus_socket",
        "/dev/dri/renderD128",
        "/dev/nvidia0",
    ):
        denied(lambda path=path: _read_file(path))
    _stage(stage, 97)
    denied(_write_input)
    for directory in ("/tmp", "/var/tmp"):
        denied(lambda directory=directory: _write_scratch(directory))
    _stage(stage, 98)
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
        denied(lambda family=family: socket.socket(family, socket.SOCK_STREAM))
    _stage(stage, 99)
    denied(_fork)
    if profile.allow_threads:
        _stage(stage, 100)
        for name in ("/model/model.safetensors", "/runtime/torch/__init__.py"):
            denied(lambda name=name: _append(name))
        _stage(stage, 101)
        _thread_boundary(profile.tasks)
    _stage(stage, 102)
    try:
        bytearray(profile.address_space * 2)
    except MemoryError:
        pass
    else:
        raise IsolationError("Missing allocation limit")


def _append(path: str) -> None:
    with open(path, "ab") as stream:
        stream.write(b"tamper")


def _write_scratch(directory: str) -> None:
    # Fresh exclusive creation tests the directory, not a pre-existing leaf.
    descriptor, name = tempfile.mkstemp(dir=directory)
    try:
        try:
            os.unlink(name)
        finally:
            os.close(descriptor)
    except OSError:
        # Creation already succeeded: cleanup errors cannot count as denial.
        raise IsolationError("Scratch probe cleanup failed") from None


def _thread_boundary(maximum: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    child = libc.syscall(56, 0, 0, 0, 0, 0)  # x86_64 clone without pthread flags
    if child == 0:
        os._exit(1)
    if child > 0:
        os.waitpid(child, 0)
        raise IsolationError("Clone created a process")
    if ctypes.get_errno() != errno.EPERM:
        raise IsolationError("Missing thread-only clone policy")
    if libc.syscall(435, 0, 0) != -1 or ctypes.get_errno() != errno.ENOSYS:
        raise IsolationError("Missing clone3 fallback policy")
    released = threading.Event()
    threads: list[threading.Thread] = []
    exhausted = False
    try:
        for _ in range(maximum * 2):
            thread = threading.Thread(target=released.wait)
            try:
                thread.start()
            except RuntimeError:
                exhausted = True
                break
            threads.append(thread)
        if not exhausted or not 1 <= len(threads) < maximum:
            raise IsolationError("Required thread quota unavailable")
    finally:
        released.set()
        for thread in threads:
            thread.join(timeout=2)
            if thread.is_alive():
                raise IsolationError("Thread probe cleanup failed")


def effective_controls(
    host: dict[str, Any],
    profile: CPUProfile = SYNTHETIC_CPU,
    *,
    stage: list[int] | None = None,
) -> dict[str, Any]:
    profile.require_owned()
    _stage(stage, 103)
    status = {
        key: value
        for line in Path("/proc/self/status").read_text().splitlines()
        for key, value in [line.split(":", 1)]
    }
    _stage(stage, 104)
    mounts = {
        fields[4]: fields[5].split(",")
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if len(fields := line.split()) > 6
    }
    _stage(stage, 105)
    cgroup = {
        name: Path(CONTROL_PATH, name).read_text().strip()
        for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
    }
    _stage(stage, 104)
    filesystems = {
        fields[4]: fields[fields.index("-") + 1]
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if len(fields := line.split()) > 6
    }
    _stage(stage, 106)
    report = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "groups": os.getgroups(),
        "namespaces": namespace_ids(),
        "limits": resource_limits(),
        "cgroup": cgroup,
        "no_new_privs": status["NoNewPrivs"].strip(),
        "seccomp": status["Seccomp"].strip(),
        "capabilities": {
            name: status[name].strip()
            for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        },
        "readonly": {name: "ro" in mounts.get(name, ()) for name in profile.readonly},
        "private_writable": {
            name: "rw" in mounts.get(name, ())
            and all(option in mounts[name] for option in ("nodev", "nosuid", "noexec"))
            for name in WRITABLE_MOUNTS
        },
        "environment": dict(os.environ),
        "tmpfs_bytes": {
            name: os.statvfs(name).f_frsize * os.statvfs(name).f_blocks
            if filesystems.get(name) == "tmpfs"
            else 0
            for name in WRITABLE_MOUNTS
        },
        "pid": os.getpid(),
        "visible_pids": sorted(name for name in os.listdir("/proc") if name.isdigit()),
        "extra_fds": extra_descriptors(),
        "probes": "passed",
    }
    if profile.allow_threads:
        report["thread_policy"] = "pthread-only-bounded"
    verify_controls(report, host, profile, stage=stage)
    probe_boundaries(host["canary"], profile, stage=stage)
    return report


def verify_controls(
    report: dict[str, Any],
    host: dict[str, Any],
    profile: CPUProfile = SYNTHETIC_CPU,
    *,
    stage: list[int] | None = None,
) -> None:
    """Recheck bounded evidence in coordinator; evidence is not remote attestation."""
    profile.require_owned()
    expected = {
        "uid",
        "gid",
        "groups",
        "namespaces",
        "limits",
        "cgroup",
        "no_new_privs",
        "seccomp",
        "capabilities",
        "readonly",
        "private_writable",
        "environment",
        "tmpfs_bytes",
        "pid",
        "visible_pids",
        "extra_fds",
        "probes",
    }
    if profile.allow_threads:
        expected.add("thread_policy")
    _require(type(report) is dict and set(report) == expected, stage, 80)
    _require(
        not (
            type(report["uid"]) is not int
            or not 61184 <= report["uid"] <= 65519
            or report["uid"] == host["uid"]
            or report["gid"] != report["uid"]
            or report["groups"] not in ([], [report["gid"]])
        ),
        stage,
        81,
    )
    _require(
        not (
            type(report["namespaces"]) is not dict
            or set(report["namespaces"]) != set(NAMESPACES)
            or any(
                type(report["namespaces"][name]) is not str
                or report["namespaces"][name] == host["namespaces"][name]
                for name in NAMESPACES
            )
        ),
        stage,
        82,
    )
    _require(report["limits"] == expected_limits(profile), stage, 83)
    _require(
        report["cgroup"]
        == {
            "memory.max": str(profile.memory),
            "memory.swap.max": "0",
            "pids.max": str(profile.tasks),
            "cpu.max": "100000 100000",
        },
        stage,
        84,
    )
    _require(report["no_new_privs"] == "1", stage, 85)
    _require(report["seccomp"] == "2", stage, 86)
    _require(
        report["capabilities"]
        == dict.fromkeys(
            ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"), "0000000000000000"
        ),
        stage,
        87,
    )
    _require(report["readonly"] == dict.fromkeys(profile.readonly, True), stage, 88)
    _require(
        report["private_writable"] == dict.fromkeys(WRITABLE_MOUNTS, True),
        stage,
        89,
    )
    _require(report["tmpfs_bytes"] == TMPFS_BYTES, stage, 90)
    _require(report["environment"] == dict(profile.environment), stage, 91)
    _require(report["pid"] == 1 and report["visible_pids"] == ["1"], stage, 92)
    _require(report["extra_fds"] == [], stage, 93)
    _require(report["probes"] == "passed", stage, 94)
    _require(
        not profile.allow_threads
        or report.get("thread_policy") == "pthread-only-bounded",
        stage,
        95,
    )


def _require(condition: bool, stage: list[int] | None, code: int) -> None:
    if not condition:
        _stage(stage, code)
        raise IsolationError("Required effective CPU controls unavailable")
