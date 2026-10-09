"""Every required effective control is necessary; declarations alone are not enough."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from scryntic.model_worker.controls import (
    effective_controls,
    extra_descriptors,
    probe_boundaries,
    verify_controls,
)
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


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("uid", 0, 81),
        ("namespaces", {}, 82),
        ("limits", {}, 83),
        ("cgroup", {}, 84),
        ("no_new_privs", "0", 85),
        ("seccomp", "0", 86),
        ("capabilities", {}, 87),
        ("readonly", {}, 88),
        ("private_writable", {}, 89),
        ("tmpfs_bytes", {}, 90),
        ("environment", {"SECRET": "exception-secret-sentinel"}, 91),
        ("pid", 2, 92),
        ("extra_fds", [3], 93),
        ("probes", "skipped", 94),
    ],
)
def test_control_failure_records_only_fixed_category(
    field: str,
    value: Any,
    code: int,
) -> None:
    evidence = report()
    evidence[field] = value
    stage = [73]
    owner = host()
    with pytest.raises(IsolationError) as error:
        verify_controls(evidence, owner, stage=stage)
    assert stage == [code]
    assert str(error.value) == "Required effective CPU controls unavailable"


def test_missing_field_records_shape_before_inspecting_values() -> None:
    evidence = report()
    del evidence["uid"]
    stage = [73]
    owner = host()
    with pytest.raises(IsolationError):
        verify_controls(evidence, owner, stage=stage)
    assert stage == [80]


def test_verified_controls_do_not_change_diagnostic_stage() -> None:
    stage = [73]
    verify_controls(report(), host(), stage=stage)
    assert stage == [73]


@pytest.mark.parametrize(
    "path,code",
    [("status", 103), ("mountinfo", 104), ("memory.max", 105)],
)
def test_inspection_failure_records_fixed_stage(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    code: int,
) -> None:
    def read(selected: Path) -> str:
        if selected.name == path:
            raise OSError("exception-secret-sentinel")
        return ""

    monkeypatch.setattr(Path, "read_text", read)
    stage = [73]
    owner = host()
    with pytest.raises(OSError):
        effective_controls(owner, stage=stage)
    assert stage == [code]


def test_boundary_failure_records_category_before_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scryntic.model_worker import controls

    monkeypatch.setattr(controls, "_read_file", lambda path: None)
    stage = [73]
    with pytest.raises(IsolationError, match="Required boundary is ineffective"):
        probe_boundaries("/unreadable-host-canary", stage=stage)
    assert stage == [96]


def test_descriptor_probe_includes_unlinked_secret_file() -> None:
    with tempfile.TemporaryFile() as secret:
        secret.write(b"private-inherited-fd")
        assert secret.fileno() in extra_descriptors()
