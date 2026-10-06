"""Synthetic control evidence for rejection tests; never host qualification."""

from typing import Any

from scryntic.model_worker.controls import expected_limits
from scryntic.model_worker.profile import (
    ENVIRONMENT,
    MEMORY,
    NAMESPACES,
    TASKS,
    TMPFS_BYTES,
)


def host() -> dict[str, Any]:
    return {
        "uid": 1000,
        "namespaces": {name: name + ":[1]" for name in NAMESPACES},
        "canary": "/host-secret",
    }


def report() -> dict[str, Any]:
    return {
        "uid": 62000,
        "gid": 62000,
        "groups": [],
        "namespaces": {name: name + ":[2]" for name in NAMESPACES},
        "limits": expected_limits(),
        "cgroup": {
            "memory.max": str(MEMORY),
            "memory.swap.max": "0",
            "pids.max": str(TASKS),
            "cpu.max": "100000 100000",
        },
        "no_new_privs": "1",
        "seccomp": "2",
        "capabilities": dict.fromkeys(
            ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"), "0000000000000000"
        ),
        "readonly": dict.fromkeys(
            ("/", "/python", "/app/scryntic", "/input/request", "/control"), True
        ),
        "private_writable": dict.fromkeys(("/home", "/tmp", "/output"), True),
        "tmpfs_bytes": dict(TMPFS_BYTES),
        "environment": dict(ENVIRONMENT),
        "pid": 1,
        "visible_pids": ["1"],
        "extra_fds": [],
        "probes": "passed",
    }
