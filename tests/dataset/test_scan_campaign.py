"""Every started attempt retains controller and cleanup failures as evidence."""

import argparse
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import pi05_campaign as campaign
from scripts.pi05_recover import registered_schedule


@pytest.mark.parametrize("metadata_failure", [False, True])
def test_controller_failure_survives_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata_failure: bool
) -> None:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0].endswith("systemd-run"):
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.pi05_campaign.subprocess.run", run)
    monkeypatch.setattr(
        "scripts.pi05_campaign.pwd.getpwnam",
        lambda _: SimpleNamespace(pw_uid=0, pw_gid=0),
    )
    report = tmp_path / "sample.json"
    if metadata_failure:
        report.write_text("{}")

        def denied(*args: Any) -> None:
            raise PermissionError("simulated evidence metadata failure")

        monkeypatch.setattr("scripts.pi05_campaign.os.chown", denied)
    result = campaign.sample(tmp_path, tmp_path, report, "pyarrow-scan", 0)
    assert result["status"] == "unqualified"
    assert result["controller_exception"] == "TimeoutExpired"
    assert result["controller_cleanup"]
    if metadata_failure:
        assert result["metadata_cleanup_exception"] == "PermissionError"


def test_sample_cannot_replace_the_admitted_fixture_identity() -> None:
    intent = {
        "candidate": "pyarrow-scan",
        "fixture_sha256": "a" * 64,
        "status": "started",
    }
    actual = {
        "candidate": "pyarrow-scan",
        "fixture_sha256": "b" * 64,
        "status": "qualified",
    }
    result = campaign.bind_result(intent, actual)
    assert result["status"] == "unqualified"
    assert result["admitted_fixture_sha256"] == "a" * 64
    assert result["fixture_sha256"] == "b" * 64
    assert result["reason"] == "fixture-changed-after-admission"


def campaign_case(tmp_path: Path) -> campaign._Campaign:
    protocol = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/validation/pi05/protocol-v1.json"
        ).read_text()
    )
    boot = tmp_path / "boot-id"
    boot.write_text("initial-boot")
    return campaign._Campaign(
        args=argparse.Namespace(
            evidence=tmp_path / "evidence", fixtures=tmp_path / "fixtures"
        ),
        source=tmp_path / "source",
        protocol=protocol,
        identities={"boot_id": "initial-boot"},
        fixtures={workload["id"]: "a" * 64 for workload in protocol["workloads"]},
        boot_file=boot,
        boot_id="initial-boot",
    )


def capture_attempts(
    monkeypatch: pytest.MonkeyPatch, case: campaign._Campaign
) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []

    def write(path: Path, value: dict[str, Any], *, replace: bool = False) -> None:
        events.append((path.name, deepcopy(value)))

    def launch(
        source: Path, fixture: Path, report: Path, candidate: str, sequence: int
    ) -> dict[str, Any]:
        assert events[-2][0] == f"intent-{sequence:03}.json"
        assert events[-1][0] == "campaign.json"
        assert events[-1][1]["pending"]["candidate"] == candidate
        assert len(events[-1][1]["records"]) == sequence
        events.append(("launch", (candidate, sequence)))
        return {
            "candidate": candidate,
            "status": "qualified",
            "fixture_sha256": "a" * 64,
        }

    monkeypatch.setattr(campaign, "durable_json", write)
    monkeypatch.setattr(campaign, "sample", launch)
    return events


def test_campaign_preserves_registered_order_and_intent_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = campaign_case(tmp_path)
    events = capture_attempts(monkeypatch, case)
    records = case.run()
    schedule = registered_schedule(case.protocol)
    assert len(records) == 72
    for actual, expected in zip(records, schedule, strict=True):
        assert {key: actual[key] for key in expected} == expected
    assert len([event for event in events if event[0] == "launch"]) == 72
    assert events[-1][0] == "record-071.json"


def test_candidate_failure_is_retained_and_not_launched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = campaign_case(tmp_path)
    events = capture_attempts(monkeypatch, case)
    qualified = campaign.sample

    def fail(
        source: Path, fixture: Path, report: Path, candidate: str, sequence: int
    ) -> dict[str, Any]:
        result = qualified(source, fixture, report, candidate, sequence)
        if candidate == "duckdb-2.0.0":
            result.update(status="unqualified", reason="simulated-control-failure")
        return result

    monkeypatch.setattr(campaign, "sample", fail)
    records = case.run()
    launches = [event[1] for event in events if event[0] == "launch"]
    assert sum(candidate == "duckdb-2.0.0" for candidate, _ in launches) == 1
    rejected = [record for record in records if record["candidate"] == "duckdb-2.0.0"]
    assert len(rejected) == 24
    assert rejected[0]["reason"] == "simulated-control-failure"
    assert all(
        record["reason"] == "prior-candidate-failure-not-retried"
        for record in rejected[1:]
    )
    assert len([event for event in events if event[0].startswith("intent-")]) == 72
    assert len([event for event in events if event[0].startswith("record-")]) == 72


def test_exhausted_budget_stops_before_intent_or_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = campaign_case(tmp_path)
    case.protocol["max_attempts"] = 0
    events = capture_attempts(monkeypatch, case)
    with pytest.raises(ValueError, match="budget exhausted"):
        case.run()
    assert events == []


def test_boot_change_disqualifies_and_prevents_further_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = campaign_case(tmp_path)
    events = capture_attempts(monkeypatch, case)
    qualified = campaign.sample

    def rebooted(
        source: Path, fixture: Path, report: Path, candidate: str, sequence: int
    ) -> dict[str, Any]:
        result = qualified(source, fixture, report, candidate, sequence)
        case.boot_file.write_text("changed-boot")
        return result

    monkeypatch.setattr(campaign, "sample", rebooted)
    records = case.run()
    assert len([event for event in events if event[0] == "launch"]) == 1
    assert len(records) == 72
    assert all(record["status"] == "unqualified" for record in records)
    assert all(record["reason"] == "boot-identity-changed" for record in records)


def test_failed_durable_checkpoint_cannot_start_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = campaign_case(tmp_path)
    events = capture_attempts(monkeypatch, case)
    write = campaign.durable_json

    def failed(path: Path, value: dict[str, Any], *, replace: bool = False) -> None:
        if path.name == "campaign.json":
            raise OSError("simulated fsync failure")
        write(path, value, replace=replace)

    monkeypatch.setattr(campaign, "durable_json", failed)
    with pytest.raises(OSError, match="fsync"):
        case.run()
    assert [event[0] for event in events] == ["intent-000.json"]
