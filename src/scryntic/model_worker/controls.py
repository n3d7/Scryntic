"""Effective OS controls checked by trusted bootstrap before worker code."""

import errno
import os
import resource
import socket
from pathlib import Path
from typing import Any

from scryntic.model_worker.profile import (
    CONTROL_PATH,
    CPU_SECONDS,
    ENVIRONMENT,
    MEMORY,
    NAMESPACES,
    OUTPUT_BYTES,
    READONLY_MOUNTS,
    REQUEST_PATH,
    TASKS,
    TMPFS_BYTES,
    WRITABLE_MOUNTS,
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


def expected_limits() -> dict[str, list[int]]:
    return {
        name: [value, value]
        for name, value in (
            ("AS", MEMORY),
            ("CPU", CPU_SECONDS),
            ("CORE", 0),
            ("FSIZE", OUTPUT_BYTES),
            ("NOFILE", 32),
            ("NPROC", 0),
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


def probe_boundaries(host_canary: str) -> None:
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
    denied(_write_input)
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
        denied(lambda family=family: socket.socket(family, socket.SOCK_STREAM))
    denied(_fork)
    try:
        bytearray(MEMORY * 2)
    except MemoryError:
        pass
    else:
        raise IsolationError("Missing allocation limit")


def effective_controls(host: dict[str, Any]) -> dict[str, Any]:
    status = {
        key: value
        for line in Path("/proc/self/status").read_text().splitlines()
        for key, value in [line.split(":", 1)]
    }
    mounts = {
        fields[4]: fields[5].split(",")
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if len(fields := line.split()) > 6
    }
    cgroup = {
        name: Path(CONTROL_PATH, name).read_text().strip()
        for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
    }
    filesystems = {
        fields[4]: fields[fields.index("-") + 1]
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if len(fields := line.split()) > 6
    }
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
        "readonly": {name: "ro" in mounts.get(name, ()) for name in READONLY_MOUNTS},
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
    verify_controls(report, host)
    probe_boundaries(host["canary"])
    return report


def verify_controls(report: dict[str, Any], host: dict[str, Any]) -> None:
    """Recheck bounded evidence in coordinator; evidence is not remote attestation."""
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
    if (
        type(report) is not dict
        or set(report) != expected
        or type(report["uid"]) is not int
        or not 61184 <= report["uid"] <= 65519
        or report["uid"] == host["uid"]
        or report["gid"] != report["uid"]
        or report["groups"] not in ([], [report["gid"]])
        or type(report["namespaces"]) is not dict
        or set(report["namespaces"]) != set(NAMESPACES)
        or any(
            type(report["namespaces"][name]) is not str
            or report["namespaces"][name] == host["namespaces"][name]
            for name in NAMESPACES
        )
        or report["limits"] != expected_limits()
        or report["cgroup"]
        != {
            "memory.max": str(MEMORY),
            "memory.swap.max": "0",
            "pids.max": str(TASKS),
            "cpu.max": "100000 100000",
        }
        or report["no_new_privs"] != "1"
        or report["seccomp"] != "2"
        or report["capabilities"]
        != dict.fromkeys(
            ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"), "0000000000000000"
        )
        or report["readonly"] != dict.fromkeys(READONLY_MOUNTS, True)
        or report["private_writable"] != dict.fromkeys(WRITABLE_MOUNTS, True)
        or report["tmpfs_bytes"] != TMPFS_BYTES
        or report["environment"] != ENVIRONMENT
        or report["pid"] != 1
        or report["visible_pids"] != ["1"]
        or report["extra_fds"] != []
        or report["probes"] != "passed"
    ):
        raise IsolationError("Required effective CPU controls unavailable")
