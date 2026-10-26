"""Only complete service accounting and successful cleanup can qualify a sample."""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import pi05_campaign as campaign


@dataclass
class SampleCase:
    root: Path
    report: Path
    cgroup: Path
    report_bytes: bytes
    state: dict[str, str]
    commands: list[list[str]] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    stop_code: int = 0
    stop_timeout: bool = False
    metadata_failure: bool = False

    def invoke(self) -> dict[str, Any]:
        return campaign.sample(
            self.root / "source", self.root / "fixture", self.report, "pyarrow-scan", 3
        )

    def assert_cleanup_and_unchanged_report(self, result: dict[str, Any]) -> None:
        unit = result["unit"]
        assert self.commands[1:] == [
            ["/usr/bin/systemctl", "stop", unit],
            ["/usr/bin/systemctl", "reset-failed", unit],
        ]
        assert self.report.read_bytes() == self.report_bytes


@pytest.fixture
def sample_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SampleCase:
    report = tmp_path / "sample.json"
    # Worker output cannot supply authoritative cgroup metrics or service time.
    report_bytes = json.dumps(
        {
            "candidate": "pyarrow-scan",
            "status": "qualified",
            "fixture_sha256": "a" * 64,
            "cgroup_memory_peak_bytes": 1,
            "full_job_wall_ns": 1,
        }
    ).encode()
    report.write_bytes(report_bytes)
    case = SampleCase(
        root=tmp_path,
        report=report,
        cgroup=tmp_path / "synthetic-cgroup",
        report_bytes=report_bytes,
        state={
            "SubState": "exited",
            "Result": "success",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
            "ExecMainStartTimestampMonotonic": "1500",
            "ExecMainExitTimestampMonotonic": "6500",
            "MemoryPeak": "100663296",
            "IOReadBytes": "0",
            "IOWriteBytes": "4096",
            "CPUUsageNSec": "2000000",
            "ControlGroup": "/synthetic-job",
            "MainPID": "0",
        },
    )

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        case.commands.append(command)
        assert command[0] in ("/usr/bin/systemd-run", "/usr/bin/systemctl")
        if command[:2] == ["/usr/bin/systemctl", "stop"]:
            if case.stop_timeout:
                raise subprocess.TimeoutExpired(command, 15)
            return subprocess.CompletedProcess(command, case.stop_code, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def state(*arguments: str) -> dict[str, str]:
        assert arguments[0] == "show"
        return dict(case.state)

    def local_path(value: str) -> Path:
        if value.startswith("/sys/fs/cgroup"):
            return case.cgroup
        return Path(value)

    def chown(*arguments: Any) -> None:
        if case.metadata_failure:
            raise PermissionError("simulated evidence metadata failure")

    ticks = iter((1_000_000, 41_000_000))
    monkeypatch.setattr(campaign, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(campaign, "systemctl", state)
    monkeypatch.setattr(campaign, "Path", local_path)
    monkeypatch.setattr(
        campaign,
        "time",
        SimpleNamespace(
            perf_counter_ns=lambda: next(ticks),
            monotonic=lambda: 0.0,
            sleep=case.sleeps.append,
        ),
    )
    monkeypatch.setattr(
        campaign,
        "pwd",
        SimpleNamespace(
            getpwnam=lambda _: SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        ),
    )
    monkeypatch.setattr(campaign, "os", SimpleNamespace(getpid=os.getpid, chown=chown))
    return case


@pytest.mark.parametrize("cgroup_state", ["removed", "empty", "unassigned"])
def test_latched_service_accounting_qualifies_without_polling_time(
    sample_case: SampleCase, cgroup_state: str
) -> None:
    if cgroup_state == "empty":
        sample_case.cgroup.mkdir()
        (sample_case.cgroup / "cgroup.procs").write_text("")
    if cgroup_state == "unassigned":
        sample_case.state["ControlGroup"] = ""
    result = sample_case.invoke()
    assert result["status"] == "qualified"
    assert result["cgroup_empty_before_stop"] is True
    assert result["cgroup_memory_peak_bytes"] == 96 * 1024 * 1024
    assert result["cgroup_io_read_bytes"] == 0
    assert result["cgroup_io_write_bytes"] == 4096
    assert result["full_job_cpu_ns"] == 2_000_000
    assert result["full_job_wall_ns"] == 5_000_000
    assert result["controller_wall_ns"] == 40_000_000
    assert result["controller_cleanup"] == [
        {"operation": "stop", "exit_code": 0},
        {"operation": "reset-failed", "exit_code": 0},
    ]
    sample_case.assert_cleanup_and_unchanged_report(result)


@pytest.mark.parametrize(
    "metric", ["MemoryPeak", "IOReadBytes", "IOWriteBytes", "CPUUsageNSec"]
)
@pytest.mark.parametrize("value", [None, "[not set]"])
def test_worker_success_cannot_replace_missing_accounting(
    sample_case: SampleCase, metric: str, value: str | None
) -> None:
    if value is None:
        sample_case.state.pop(metric)
    else:
        sample_case.state[metric] = value
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "missing-cgroup-accounting"
    sample_case.assert_cleanup_and_unchanged_report(result)


@pytest.mark.parametrize(
    "begin,end", [("0", "6500"), ("1500", "1500"), ("6500", "1500")]
)
def test_invalid_service_duration_cannot_qualify(
    sample_case: SampleCase, begin: str, end: str
) -> None:
    sample_case.state["ExecMainStartTimestampMonotonic"] = begin
    sample_case.state["ExecMainExitTimestampMonotonic"] = end
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "missing-service-duration"
    sample_case.assert_cleanup_and_unchanged_report(result)


@pytest.mark.parametrize("remaining", ["main", "child"])
def test_remaining_owner_or_child_requires_cleanup_and_disqualification(
    sample_case: SampleCase, remaining: str
) -> None:
    if remaining == "main":
        sample_case.state["MainPID"] = "123"
    else:
        sample_case.cgroup.mkdir()
        (sample_case.cgroup / "cgroup.procs").write_text("456\n")
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "worker-not-reaped"
    sample_case.assert_cleanup_and_unchanged_report(result)


@pytest.mark.parametrize(
    "field_name,value",
    [("Result", "timeout"), ("ExecMainCode", "2"), ("ExecMainStatus", "9")],
)
def test_failed_manager_overrides_qualified_worker_report(
    sample_case: SampleCase, field_name: str, value: str
) -> None:
    sample_case.state[field_name] = value
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "outer-job-failed"
    sample_case.assert_cleanup_and_unchanged_report(result)


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_stop_disqualifies_but_still_attempts_reset(
    sample_case: SampleCase, timeout: bool
) -> None:
    sample_case.stop_code = 7
    sample_case.stop_timeout = timeout
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "cleanup-failed"
    expected = (
        {"operation": "stop", "exception": "TimeoutExpired"}
        if timeout
        else {"operation": "stop", "exit_code": 7}
    )
    assert result["controller_cleanup"][0] == expected
    assert result["controller_cleanup"][1] == {
        "operation": "reset-failed",
        "exit_code": 0,
    }
    sample_case.assert_cleanup_and_unchanged_report(result)


def test_metadata_failure_disqualifies_after_unit_cleanup(
    sample_case: SampleCase,
) -> None:
    sample_case.metadata_failure = True
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "metadata-cleanup-failed"
    assert result["metadata_cleanup_exception"] == "PermissionError"
    sample_case.assert_cleanup_and_unchanged_report(result)


def test_nonterminal_manager_hits_bounded_wait_and_still_cleans_up(
    sample_case: SampleCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_case.state["SubState"] = "running"
    ticks = iter((0.0, 0.0, 196.0))
    monkeypatch.setattr("scripts.pi05_campaign.time.monotonic", lambda: next(ticks))
    result = sample_case.invoke()
    assert result["status"] == "unqualified"
    assert result["reason"] == "controller-exception"
    assert result["controller_exception"] == "TimeoutError"
    assert sample_case.sleeps == [0.25]
    sample_case.assert_cleanup_and_unchanged_report(result)
