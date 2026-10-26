"""Predeclared paired process-run comparison; not forecast significance testing."""

import random
from statistics import median
from typing import Any

IDENTITIES = (
    "fixture_sha256",
    "source_tree_sha256",
    "dependency_lock_sha256",
    "runtime_sha256",
    "python_sha256",
    "protocol_sha256",
    "source_archive_sha256",
    "boot_id",
)


def _groups(
    records: list[dict[str, Any]], workload: str, protocol: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    return {
        candidate: sorted(
            [
                r
                for r in records
                if r["workload"] == workload
                and r["candidate"] == candidate
                and not r["warmup"]
            ],
            key=lambda r: r["run"],
        )
        for candidate in protocol["candidates"]
    }


def _candidate(
    candidate: str,
    runs: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    records: list[dict[str, Any]],
    workload: dict[str, Any],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    reference = (baseline[0]["rows_sha256"], baseline[0]["horizons"])
    expected_identity = tuple(baseline[0].get(key) for key in IDENTITIES)
    failed = any(
        r["candidate"] == candidate and r["status"] != "qualified" for r in records
    )
    if (
        failed
        or len(runs) != len(baseline)
        or any(r["status"] != "qualified" for r in runs)
    ):
        return {"status": "unqualified"}
    if any((r["rows_sha256"], r["horizons"]) != reference for r in runs):
        raise ValueError("Candidate semantic drift")
    if any(
        tuple(r.get(key) for key in IDENTITIES) != expected_identity
        or r.get("rows") != workload["rows"]
        or r.get("gaps") is not workload["gaps"]
        for r in runs
    ):
        raise ValueError("Candidate qualification identity drift")
    ratios = [
        r["full_job_wall_ns"] / b["full_job_wall_ns"]
        for r, b in zip(runs, baseline, strict=True)
    ]
    randomizer = random.Random(protocol["order_seed"])
    samples = sorted(
        median(randomizer.choices(ratios, k=len(ratios))) for _ in range(10000)
    )
    memory_ratio = max(r["cgroup_memory_peak_bytes"] for r in runs) / max(
        r["cgroup_memory_peak_bytes"] for r in baseline
    )
    accepted = (
        samples[9750] <= 1 - protocol["minimum_wall_reduction_fraction"]
        and memory_ratio <= 1 + protocol["memory_regression_fraction"]
    )
    return {
        "status": "qualified",
        "median_wall_ns": int(median(r["full_job_wall_ns"] for r in runs)),
        "median_ratio": str(median(ratios)),
        "paired_ratio_95_interval": [str(samples[250]), str(samples[9750])],
        "peak_cgroup_memory_bytes": max(r["cgroup_memory_peak_bytes"] for r in runs),
        "peak_memory_ratio": str(memory_ratio),
        "meets_adoption_gate": accepted,
    }


def _workload(
    records: list[dict[str, Any]], workload: dict[str, Any], protocol: dict[str, Any]
) -> dict[str, Any]:
    groups = _groups(records, workload["id"], protocol)
    baseline = groups["pyarrow-pages"]
    if len(baseline) != protocol["measured_process_runs"] or any(
        r["status"] != "qualified" for r in baseline
    ):
        return {"status": "baseline-incomplete"}
    reference = [(r["rows_sha256"], r["horizons"]) for r in baseline]
    expected_identity = tuple(baseline[0].get(key) for key in IDENTITIES)
    if any(type(value) is not str or not value for value in expected_identity):
        raise ValueError("Missing qualification identity")
    if any(value != reference[0] for value in reference):
        raise ValueError("Baseline semantic drift")
    return {
        candidate: _candidate(
            candidate,
            runs,
            baseline,
            records,
            workload,
            protocol,
        )
        for candidate, runs in groups.items()
    }


def summarize(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {
        workload["id"]: _workload(records, workload, protocol)
        for workload in protocol["workloads"]
    }
    qualified = all(
        c.get("pyarrow-scan", {}).get("meets_adoption_gate", False)
        for c in comparisons.values()
    )
    return {
        "schema": "scryntic.pi05.decision.v1",
        "comparisons": comparisons,
        "selected": "pyarrow-scan" if qualified else "pyarrow-pages",
        "reason": "minimum sufficient existing PyArrow; no additional product engine or privileges"
        if qualified
        else "retained current path; registered evidence does not admit replacement",
        "limits": "synthetic registered scan jobs on one host; no capacity, model or arbitrary-query claim",
    }
