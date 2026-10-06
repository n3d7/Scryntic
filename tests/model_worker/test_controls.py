"""Every required effective control is necessary; declarations alone are not enough."""

import tempfile
from typing import Any

import pytest

from scryntic.model_worker.controls import extra_descriptors, verify_controls
from scryntic.model_worker.profile import IsolationError
from tests.model_worker.helpers import host, report


@pytest.mark.parametrize("field", list(report()))
def test_missing_control_rejects_before_execution(field: str) -> None:
    evidence = report()
    del evidence[field]
    owner = host()
    with pytest.raises(IsolationError):
        verify_controls(evidence, owner)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", 1000),
        ("uid", 0),
        ("gid", 1000),
        ("groups", [1000]),
        ("pid", 2000),
        ("visible_pids", ["1", "2"]),
        ("extra_fds", [3]),
        ("seccomp", "0"),
        ("no_new_privs", "0"),
        ("probes", "skipped"),
        ("environment", {"F21_SECRET_SENTINEL": "private"}),
    ],
)
def test_ineffective_controls_reject(field: str, value: Any) -> None:
    evidence = report()
    evidence[field] = value
    owner = host()
    with pytest.raises(IsolationError):
        verify_controls(evidence, owner)


@pytest.mark.parametrize(
    "field",
    [
        "limits",
        "cgroup",
        "capabilities",
        "readonly",
        "private_writable",
        "tmpfs_bytes",
        "namespaces",
    ],
)
def test_each_nested_control_rejects(field: str) -> None:
    original = report()[field]
    owner = host()
    for member in original:
        evidence = report()
        evidence[field][member] = None
        with pytest.raises(IsolationError):
            verify_controls(evidence, owner)


def test_reference_evidence_passes() -> None:
    verify_controls(report(), host())


def test_descriptor_probe_includes_unlinked_secret_file() -> None:
    with tempfile.TemporaryFile() as secret:
        secret.write(b"private-inherited-fd")
        assert secret.fileno() in extra_descriptors()
