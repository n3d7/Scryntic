"""Closed scan operations, reached only after the existing no-thread bootstrap."""

import os
import time
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import (
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    primitive_document,
)
from scryntic.imports.protocol import ImportLimits


def _duckdb_rows() -> list[dict[str, Any]]:
    os.write(2, b"pi05:native-import\n")
    duckdb = import_module("_duckdb")

    if duckdb.__version__ != "2.0.0":
        raise ValueError("Unexpected candidate version")
    connection = duckdb.connect(
        config={
            "threads": 1,
            "memory_limit": "512MiB",
            "temp_directory": "/no-spill",
            "max_temp_directory_size": "0B",
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "allow_unsigned_extensions": False,
            "allow_community_extensions": False,
        }
    )
    try:
        # These settings are runtime-only in DuckDB 2.0.0. This trusted setup
        # happens before any file/query operation and then becomes immutable.
        connection.execute("SET allowed_paths = ['/input/parquet']")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        os.write(2, b"pi05:native-query\n")
        # The only workload query is application-owned, not a request string.
        cursor = connection.execute(
            "SELECT * FROM read_parquet('/input/parquet') ORDER BY \"offset\", semantic_revision"
        )
        names = [d[0] for d in cursor.description]
        rows = [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]
        os.write(2, b"pi05:native-denials\n")
        for query in (
            "SET enable_external_access = true",
            "INSTALL httpfs",
            "SELECT * FROM read_parquet('/unapproved')",
        ):
            try:
                connection.execute(query)
            except Exception:
                continue
            raise ValueError("Candidate effective deny missing")
        return rows
    finally:
        connection.close()


def run(arguments: list[str]) -> None:
    limits = ImportLimits(
        max_encoded_bytes=int(arguments[0]),
        max_decoded_bytes=int(arguments[1]),
        max_records=int(arguments[2]),
    )
    data = Path("/input/request").read_bytes()
    request = primitive_document(data, MAX_REQUEST_BYTES)
    if request.get("protocol") != 1:
        raise ValueError("Invalid qualification protocol")
    candidate = request.pop("candidate")
    if candidate not in ("pyarrow-pages", "pyarrow-scan", "duckdb-2.0.0"):
        raise ValueError("Unregistered candidate")
    if request.get("operation") == "cancel-probe":
        time.sleep(60)
        raise ValueError("Deadline probe survived")
    if set(request) != {
        "protocol",
        "operation",
        "manifest_sha256",
        "dataset_schema",
        "row_count",
        "offset",
        "limit",
    } or request["operation"] not in ("scan", "inspect"):
        raise ValueError("Invalid qualification request")
    from scryntic.dataset.worker import _inspect, _primitive_row

    # Native candidate has the same complete Arrow integrity/lineage oracle cost.
    # This never replaces whole-object assurance with a fast SELECT/LIMIT.
    usage: dict[str, int] = {}
    rows = _inspect(request, limits, usage=usage)["rows"]
    if candidate == "duckdb-2.0.0":
        native = [_primitive_row(row) for row in _duckdb_rows()]
        if native != rows:
            raise ValueError("Candidate semantic mismatch")
        rows = native
        usage["scan_count"] += 1
    body: dict[str, Any] = {
        "rows": rows,
        "usage": usage,
        "request_sha256": sha256(data).hexdigest(),
    }
    output = canonical_json_bytes(body)
    if len(output) > MAX_RESULT_BYTES:
        raise ValueError("Qualification result bound")
    view = memoryview(output)
    while view:
        count = os.write(1, view)
        if count <= 0:
            raise ValueError("Qualification IPC failed")
        view = view[count:]
