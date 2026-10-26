"""Adoption requires every workload, exact semantics and the registered memory gate."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scryntic.analytics.decision import summarize


def examples() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    protocol = {
        "workloads": [{"id": "fixture", "rows": 129, "gaps": False}],
        "candidates": ["pyarrow-pages", "pyarrow-scan"],
        "measured_process_runs": 7,
        "order_seed": 6505,
        "minimum_wall_reduction_fraction": 0.25,
        "memory_regression_fraction": 0.1,
    }
    records = [
        {
            "workload": "fixture",
            "candidate": candidate,
            "warmup": False,
            "run": run,
            "status": "qualified",
            "rows_sha256": "a" * 64,
            "horizons": {},
            "full_job_wall_ns": wall,
            "cgroup_memory_peak_bytes": 1000,
        }
        for candidate, wall in (("pyarrow-pages", 100), ("pyarrow-scan", 50))
        for run in range(1, 8)
    ]
    for record in records:
        record.update(rows=129, gaps=False)
        for name in (
            "fixture_sha256",
            "source_tree_sha256",
            "dependency_lock_sha256",
            "runtime_sha256",
            "python_sha256",
            "protocol_sha256",
            "source_archive_sha256",
            "boot_id",
        ):
            record[name] = "a" * 64
    return records, protocol


def test_gate_requires_complete_runs_and_exact_outputs() -> None:
    records, protocol = examples()
    assert summarize(records, protocol)["selected"] == "pyarrow-scan"
    bad = deepcopy(records)
    bad[-1]["rows_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="semantic drift"):
        summarize(bad, protocol)
    bad = deepcopy(records)
    bad[-1]["status"] = "unqualified"
    assert summarize(bad, protocol)["selected"] == "pyarrow-pages"


def test_memory_regression_or_incomplete_baseline_prevents_adoption() -> None:
    records, protocol = examples()
    records[-1]["cgroup_memory_peak_bytes"] = 1200
    assert summarize(records, protocol)["selected"] == "pyarrow-pages"
    assert summarize(records[1:], protocol)["selected"] == "pyarrow-pages"


def test_fixture_identity_drift_and_warmup_failure_prevent_adoption() -> None:
    records, protocol = examples()
    records[-1]["fixture_sha256"] = "different-physical-fixture"
    with pytest.raises(ValueError, match="identity"):
        summarize(records, protocol)
    records, protocol = examples()
    records.append(
        {
            "workload": "fixture",
            "candidate": "pyarrow-scan",
            "warmup": True,
            "status": "unqualified",
        }
    )
    assert summarize(records, protocol)["selected"] == "pyarrow-pages"


def test_published_registered_decision_recomputes_exactly() -> None:
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads((root / "docs/validation/pi05/results-v1.json").read_text())
    protocol = json.loads((root / "docs/validation/pi05/protocol-v1.json").read_text())
    records = [
        evidence["identities"] | record | {"boot_id": record["environment_epoch"]}
        for record in evidence["records"]
    ]
    assert summarize(records, protocol) == evidence["decision"]
