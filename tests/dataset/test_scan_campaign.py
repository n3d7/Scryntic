"""Every started attempt retains controller and cleanup failures as evidence."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import pi05_campaign as campaign


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
