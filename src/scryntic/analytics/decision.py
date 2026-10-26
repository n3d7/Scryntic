"""Predeclared paired process-run comparison; not forecast significance testing."""

import random
from statistics import median
from typing import Any


def summarize(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    qualified = True
    for workload in protocol["workloads"]:
        name = workload["id"]
        identities = (
            "fixture_sha256",
            "source_tree_sha256",
            "dependency_lock_sha256",
            "runtime_sha256",
            "python_sha256",
            "protocol_sha256",
            "source_archive_sha256",
            "boot_id",
        )
        groups = {
            candidate: sorted(
                [
                    r
                    for r in records
                    if r["workload"] == name
                    and r["candidate"] == candidate
                    and not r["warmup"]
                ],
                key=lambda r: r["run"],
            )
            for candidate in protocol["candidates"]
        }
        baseline = groups["pyarrow-pages"]
        if len(baseline) != protocol["measured_process_runs"] or any(
            r["status"] != "qualified" for r in baseline
        ):
            qualified = False
            comparisons[name] = {"status": "baseline-incomplete"}
            continue
        reference = [(r["rows_sha256"], r["horizons"]) for r in baseline]
        expected_identity = tuple(baseline[0].get(key) for key in identities)
        if any(type(value) is not str or not value for value in expected_identity):
            raise ValueError("Missing qualification identity")
        if any(value != reference[0] for value in reference):
            raise ValueError("Baseline semantic drift")
        comparisons[name] = {}
        for candidate, runs in groups.items():
            failed = any(
                r["candidate"] == candidate and r["status"] != "qualified"
                for r in records
            )
            if failed:
                comparisons[name][candidate] = {"status": "unqualified"}
                continue
            if len(runs) != len(baseline) or any(
                r["status"] != "qualified" for r in runs
            ):
                comparisons[name][candidate] = {"status": "unqualified"}
                continue
            if any((r["rows_sha256"], r["horizons"]) != reference[0] for r in runs):
                raise ValueError("Candidate semantic drift")
            if any(
                tuple(r.get(key) for key in identities) != expected_identity
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
            comparisons[name][candidate] = {
                "status": "qualified",
                "median_wall_ns": int(median(r["full_job_wall_ns"] for r in runs)),
                "median_ratio": str(median(ratios)),
                "paired_ratio_95_interval": [str(samples[250]), str(samples[9750])],
                "peak_cgroup_memory_bytes": max(
                    r["cgroup_memory_peak_bytes"] for r in runs
                ),
                "peak_memory_ratio": str(memory_ratio),
                "meets_adoption_gate": accepted,
            }
        if (
            not comparisons[name]
            .get("pyarrow-scan", {})
            .get("meets_adoption_gate", False)
        ):
            qualified = False
    return {
        "schema": "scryntic.pi05.decision.v1",
        "comparisons": comparisons,
        "selected": "pyarrow-scan" if qualified else "pyarrow-pages",
        "reason": "minimum sufficient existing PyArrow; no additional product engine or privileges"
        if qualified
        else "retained current path; registered evidence does not admit replacement",
        "limits": "synthetic registered scan jobs on one host; no capacity, model or arbitrary-query claim",
    }
