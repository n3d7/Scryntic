"""Administrative recovery cannot erase an attempt or mix partial boot epochs."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.pi05_recover import claim_once, recovery_plan, registered_schedule


def checkpoint() -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = {
        "workloads": [
            {"id": "hourly-512", "rows": 512, "gaps": False},
            {"id": "hourly-4096", "rows": 4096, "gaps": False},
            {"id": "hourly-4096-gaps", "rows": 4096, "gaps": True},
        ],
        "warmup_runs": 1,
        "measured_process_runs": 7,
        "candidates": ["pyarrow-pages", "pyarrow-scan", "duckdb-2.0.0"],
        "order_seed": 6505,
        "max_attempts": 72,
    }
    schedule = registered_schedule(protocol)
    previous = {
        "state": "incomplete",
        "records": [r | {"status": "qualified"} for r in schedule[:59]],
        "pending": schedule[59] | {"status": "started"},
    }
    return previous, protocol


def test_recovery_retains_complete_workloads_and_all_consumed_attempts() -> None:
    previous, protocol = checkpoint()
    retained, excluded, scheduled = recovery_plan(previous, protocol)
    assert len(retained) == 48
    assert len(excluded) == 12
    assert len(scheduled) == 24
    assert len(previous["records"]) + 1 + len(scheduled) == 84
    assert excluded[-1]["status"] == "operator-cancelled"
    assert {r["workload"] for r in retained} == {"hourly-512", "hourly-4096"}
    assert {r["workload"] for r in scheduled} == {"hourly-4096-gaps"}
    assert previous["pending"]["status"] == "started"


@pytest.mark.parametrize("damage", ["order", "failed", "count", "pending"])
def test_recovery_rejects_other_checkpoint_or_engine_failure(damage: str) -> None:
    previous, protocol = checkpoint()
    previous = deepcopy(previous)
    if damage == "order":
        previous["records"][0]["candidate"] = "not-registered"
    elif damage == "failed":
        previous["records"][50]["status"] = "unqualified"
    elif damage == "count":
        previous["records"].pop()
    else:
        previous["pending"]["run"] = 7
    with pytest.raises(ValueError, match="checkpoint"):
        recovery_plan(previous, protocol)


def test_claim_prevents_another_campaign_with_a_fresh_evidence_directory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixed-state" / "checkpoint-digest.json"
    claim_once(path, {"evidence": "first", "limit": 84})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        claim_once(path, {"evidence": "second", "limit": 84})
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600
