"""Complete bounded scan jobs; fixture preparation is excluded and labelled."""

import json
import resource
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from scryntic.analytics.launcher import CANDIDATES, ScanDecoder
from scryntic.analytics.scans import compare_rows, horizon_inputs
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import (
    MAX_RESULT_BYTES,
    primitive_document,
    validate_rows,
)
from scryntic.imports.protocol import ImportLimits


def run_fixture(path: Path, candidate: str) -> dict[str, Any]:
    if candidate not in CANDIDATES:
        raise ValueError("Unregistered scan candidate")
    started = time.perf_counter_ns()
    cpu = time.process_time_ns()
    data = (path / "fixture.json").read_bytes()
    fixture = primitive_document(data, MAX_RESULT_BYTES)
    rows: list[dict[str, Any]] = []
    decoder = ScanDecoder(ImportLimits())
    count = fixture["row_count"]
    step = 128 if candidate == "pyarrow-pages" else max(1, count)
    usage = {"decoded_bytes": 0, "rows_considered": 0, "scan_count": 0}
    wire_bytes = encoded = 0
    for offset in range(0, max(1, count), step):
        # Deliberately retain repeated input reads/hashes/seals in the unchanged
        # paginated comparator, matching independent snapshot requests.
        manifest = (path / "manifest.json").read_bytes()
        parquet = (path / "dataset.parquet").read_bytes()
        if (
            sha256(manifest).hexdigest() != fixture["manifest_sha256"]
            or sha256(parquet).hexdigest() != fixture["parquet_sha256"]
        ):
            raise ValueError("Registered fixture bytes changed")
        request = {
            "protocol": 1,
            "operation": "inspect" if candidate == "pyarrow-pages" else "scan",
            "manifest_sha256": fixture["manifest_sha256"],
            "dataset_schema": fixture["dataset_schema"],
            "row_count": count,
            "offset": offset,
            "limit": step,
        }
        response = decoder.qualify(request, manifest, parquet, candidate)
        if set(response) != {"request_sha256", "rows", "usage"}:
            raise ValueError("Invalid qualified response")
        page = response["rows"]
        if type(page) is not list or len(page) != min(step, max(0, count - offset)):
            raise ValueError("Qualified result count mismatch")
        if candidate == "pyarrow-pages":
            validate_rows(page, evolved=True)
        rows.extend(page)
        for key in usage:
            value = response["usage"][key]
            if type(value) is not int or value < 0:
                raise ValueError("Invalid qualified usage")
            usage[key] += value
        wire_bytes += len(canonical_json_bytes(response))
        encoded += len(manifest) + len(parquet)
    compare_rows(rows, fixture)
    rows_body: dict[str, Any] = {"rows": rows}
    result = {
        "schema": "scryntic.pi05.scan-result.v1",
        "candidate": candidate,
        "fixture_sha256": sha256(data).hexdigest(),
        "rows": count,
        "rows_sha256": sha256(canonical_json_bytes(rows_body)).hexdigest(),
        "horizons": horizon_inputs(rows),
        "usage": usage,
        "job_body_wall_ns": time.perf_counter_ns() - started,
        "coordinator_cpu_ns": time.process_time_ns() - cpu,
        "coordinator_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "max_worker_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "worker_cpu_ns": int(
            (
                resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime
                + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime
            )
            * 10**9
        ),
        "encoded_input_bytes": encoded,
        "output_bytes": wire_bytes,
        "spill_bytes": 0,
        "spill_policy": "no writable mount or file creation; unsupported spill fails closed",
        "decoded_scope": "actual Arrow batch nbytes; additional DuckDB decoded allocations not separately observable",
        "cache": "fresh process; OS cache unchanged; validation hashing included",
        "status": "qualified",
        "cleanup": "bounded_process reaped every worker before return",
    }
    # Exercise canonical serialization/complete output consumption in the job.
    return json.loads(canonical_json_bytes(result))  # type: ignore[no-any-return]
