"""Fixed trusted analytical transforms, loaded only after OS restriction."""

import base64
import json
import os
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scryntic.application.sources import BYBIT_CANDLE_SCHEMA
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.protocol import (
    MAX_DATASET_BYTES,
    MAX_INSPECT_ROWS,
    MAX_PROVENANCE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    clock_from,
    primitive_document,
)
from scryntic.dataset.recipes import (
    CoverageClaim,
    RecipePolicy,
    apply_policy,
    derive_rows,
)
from scryntic.dataset.schemas import (
    DATASET_SCHEMA,
    EVOLVED_DATASET_SCHEMA,
    EVOLVED_MANIFEST_SCHEMA,
    F18_RECIPE_SCHEMA,
    schema_from,
    schema_projection,
)
from scryntic.dataset.selection import SourceInput
from scryntic.dataset.snapshot import TABLE_SCHEMA, _lineage, _row
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import CANDLE_SCHEMA, INSTRUMENT_SCHEMA
from scryntic.domain.validation import integer
from scryntic.imports.protocol import ImportLimits
from scryntic.imports.worker import validated_inputs
from scryntic.normalization.candle import FAKE_CANDLE_SCHEMA


def table_schema(evolved: bool) -> pa.Schema:
    # Parquet's compliant nested-list encoding names the child "element".
    # Normalize that documented spelling, keeping metadata checks enabled.
    base = TABLE_SCHEMA.set(
        TABLE_SCHEMA.get_field_index("quality_flags"),
        pa.field("quality_flags", pa.list_(pa.field("element", pa.string()))),
    )
    if not evolved:
        return base
    return pa.schema(
        [
            *base,
            pa.field("lag_close", pa.decimal128(38, 18)),
            pa.field("label_close", pa.decimal128(38, 18)),
            pa.field("label_end_ns", pa.int64()),
        ]
    )


def _claims(values: list[dict[str, Any]]) -> tuple[CoverageClaim, ...]:
    result = []
    for item in values:
        detected = clock_from(item["detected"])
        if detected is None:
            raise ValueError("Missing coverage evidence")
        result.append(
            CoverageClaim(
                InstrumentId(**item["instrument"]),
                item["interval_ns"],
                item["start_ns"],
                item["end_ns"],
                item["status"],
                detected,
            )
        )
    return tuple(result)


def _collect(
    request: dict[str, Any], limits: ImportLimits
) -> tuple[tuple[SourceInput, ...], list[dict[str, Any]]]:
    count = request["input_count"]
    integer(count, 1)
    if count > 8 or len(request["imports"]) != count:
        raise ValueError("Input count limit")
    sources: list[SourceInput] = []
    inputs: list[dict[str, Any]] = []
    for i in range(count):
        manifest = Path(f"/input/m{i}").read_bytes()
        document, values = validated_inputs(
            Path(f"/input/m{i}"),
            Path(f"/input/r{i}"),
            Path(f"/input/n{i}"),
            limits,
        )
        if document.manifest_hash != request["imports"][i]["manifest_sha256"]:
            raise ValueError("Import selection mismatch")
        inputs.append(
            {
                "manifest_sha256": document.manifest_hash,
                "body": json.loads(manifest),
                "objects": [
                    {
                        "role": item.role.value,
                        "sha256": item.sha256,
                        "schema": schema_projection(item.format),
                        "codec": item.codec,
                        "encoded_bytes": item.encoded_bytes,
                        "decoded_bytes": item.decoded_bytes,
                        "record_count": item.record_count,
                    }
                    for item in document.body.objects
                ],
            }
        )
        raw, normalized = document.body.objects
        for ordinal, value in enumerate(values):
            outcome = value.outcome
            if (
                outcome.input_schema not in (FAKE_CANDLE_SCHEMA, BYBIT_CANDLE_SCHEMA)
                or outcome.instrument_schema != INSTRUMENT_SCHEMA
                or outcome.output_schema != CANDLE_SCHEMA
            ):
                raise ValueError("Unsupported semantic schema")
            sources.append(
                SourceInput(
                    document.manifest_hash,
                    raw.sha256,
                    normalized.sha256,
                    ordinal,
                    value,
                )
            )
    if len(sources) > limits.max_records:
        raise ValueError("Analytical row limit")
    inputs.sort(key=lambda item: item["manifest_sha256"])
    return tuple(sources), inputs


def _build(request: dict[str, Any], limits: ImportLimits) -> dict[str, Any]:
    recipe = schema_from(request["recipe"])
    F18_RECIPE_SCHEMA.require_readable(recipe)
    evolved = recipe == F18_RECIPE_SCHEMA
    policy = RecipePolicy(**request["policy"])
    cutoff = clock_from(request["cutoff"])
    clock_limits = ClockLimits(**request["environment"]["clock_limits"])
    sources, inputs = _collect(request, limits)
    selected, excluded = apply_policy(
        sources, policy, cutoff, clock_limits, _claims(request["coverage_claims"])
    )
    rows, derived = derive_rows(selected, [_row(item) for item in selected], policy)
    if evolved:
        for row in rows:
            for name in ("lag_close", "label_close", "label_end_ns"):
                row.setdefault(name, None)
    table = pa.Table.from_pylist(rows, schema=table_schema(evolved))
    if table.nbytes > limits.max_decoded_bytes:
        raise ValueError("Analytical decoded byte limit")
    sink = pa.BufferOutputStream()
    pq.write_table(
        table,
        sink,
        compression="zstd",
        version="2.6",
        row_group_size=128,
        use_dictionary=False,
        write_statistics=True,
    )
    parquet = sink.getvalue().to_pybytes()
    if not 0 < len(parquet) <= MAX_DATASET_BYTES:
        raise ValueError("Dataset encoded byte limit")
    dataset_schema = EVOLVED_DATASET_SCHEMA if evolved else DATASET_SCHEMA
    coverage_data = request["coverage_claims"]
    manifest = {
        "schema": schema_projection(EVOLVED_MANIFEST_SCHEMA),
        "dataset_schema": schema_projection(dataset_schema),
        "recipe": request["recipe"],
        "environment": request["environment"],
        "code_revision": request["environment"]["code_revision"],
        "dependency_lock_sha256": request["environment"]["dependency_lock_sha256"],
        "selection_mode": policy.mode,
        "policy": policy.projection(),
        "as_observed_cutoff": request["cutoff"],
        "clock_limits": asdict(clock_limits),
        "inputs": inputs,
        "imports": request["imports"],
        "coverage_claims": coverage_data,
        "coverage_sha256": sha256(canonical_json_bytes(coverage_data)).hexdigest(),
        "coverage": {
            "validated_input_count": len(sources),
            "selected_logical_row_count": len(rows),
            "completeness": "unknown" if not coverage_data else "explicit-claims",
        },
        "time_quality": sorted(
            {item.value.raw.envelope.receipt.quality.status for item in sources}
        ),
        "exclusions": list(excluded),
        "ordering": ["start_ns", "venue", "category", "symbol", "interval_ns"],
        "time_range_ns": None
        if not rows
        else [rows[0]["start_ns"], rows[-1]["start_ns"]],
        "row_count": len(rows),
        "rows": [
            {
                "index": i,
                "key": {
                    name: row[name]
                    for name in (
                        "start_ns",
                        "interval_ns",
                        "venue",
                        "category",
                        "symbol",
                    )
                },
                "semantic_revision": row["semantic_revision"],
                "selected": _lineage(item.selected),
                "evidence": [_lineage(value) for value in item.evidence],
            }
            for i, (row, item) in enumerate(zip(rows, selected, strict=True))
        ],
        "transformations": {
            "normalizer_versions": sorted(
                {item.value.outcome.normalizer_version for item in sources}
            ),
            "selection_recipe": request["recipe"],
            "derived_recipe": "exact-contiguous-close-v1",
        },
        "features": ["open", "high", "low", "close", "volume"]
        + (["lag_close"] if policy.lag_steps else []),
        "feature_definitions": {
            "candle": "exact decimal128(38,18); no float conversion",
            "lag_close": {
                "steps": policy.lag_steps,
                "missing": "null",
                "imputation": "none",
            },
        },
        "labels": [] if not policy.label_horizon_steps else ["label_close"],
        "label_definitions": {
            "label_close": {
                "horizon_steps": policy.label_horizon_steps,
                "value": "finalized future candle close",
                "missing": "null",
            }
        },
        "derived_lineage": derived,
        "splits": [],
        "imputation": "none",
        "writer": {
            "pyarrow": "25.0.1",
            "parquet_version": "2.6",
            "compression": "zstd",
            "row_group_size": 128,
            "use_dictionary": False,
        },
        "parquet": {
            "sha256": sha256(parquet).hexdigest(),
            "encoded_bytes": len(parquet),
            "schema": schema_projection(dataset_schema),
            "row_count": len(rows),
        },
    }
    if len(canonical_json_bytes(manifest)) > MAX_PROVENANCE_BYTES:
        raise ValueError("Dataset provenance limit")
    return {
        "manifest": manifest,
        "parquet_base64": base64.b64encode(parquet).decode("ascii"),
    }


def _primitive_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in row.items()
    }


def _inspection_file(
    request: dict[str, Any], limits: ImportLimits
) -> tuple[dict[str, Any], pq.ParquetFile]:
    data = Path("/input/manifest").read_bytes()
    manifest = primitive_document(data, MAX_PROVENANCE_BYTES)
    schema = schema_from(request["dataset_schema"])
    EVOLVED_DATASET_SCHEMA.require_readable(schema)
    integer(request["row_count"], 0)
    integer(request["offset"], 0)
    integer(request["limit"], 1)
    if request["row_count"] > limits.max_records or request["limit"] > MAX_INSPECT_ROWS:
        raise ValueError("Inspection limit")
    parquet = Path("/input/parquet").read_bytes()
    if (
        sha256(data).hexdigest() != request["manifest_sha256"]
        or sha256(parquet).hexdigest() != manifest["parquet"]["sha256"]
        or len(parquet) != manifest["parquet"]["encoded_bytes"]
    ):
        raise ValueError("Inspection identity mismatch")
    file = pq.ParquetFile(
        pa.BufferReader(parquet),
        arrow_extensions_enabled=False,
        thrift_string_size_limit=1024 * 1024,
        thrift_container_size_limit=100000,
        pre_buffer=False,
    )
    if (
        not file.schema_arrow.equals(
            table_schema(schema == EVOLVED_DATASET_SCHEMA), check_metadata=True
        )
        or file.metadata.num_rows != request["row_count"]
    ):
        raise ValueError("Inspection schema mismatch")
    return manifest, file


def _inspect(request: dict[str, Any], limits: ImportLimits) -> dict[str, Any]:
    manifest, file = _inspection_file(request, limits)
    total = decoded = 0
    rows: list[dict[str, Any]] = []
    for batch in file.iter_batches(batch_size=128, use_threads=False):
        decoded += batch.nbytes
        if decoded > limits.max_decoded_bytes:
            raise ValueError("Inspection decoded byte limit")
        for row in batch.to_pylist():
            lineage = manifest["rows"][total]
            if (
                row["semantic_revision"] != lineage["semantic_revision"]
                or row["offset"] != lineage["selected"]["identity"]["offset"]
            ):
                raise ValueError("Inspection lineage mismatch")
            if request["offset"] <= total < request["offset"] + request["limit"]:
                rows.append(_primitive_row(row))
            total += 1
    if total != request["row_count"]:
        raise ValueError("Inspection row count mismatch")
    return {"rows": rows}


def run(arguments: list[str]) -> None:
    limits = ImportLimits(
        max_encoded_bytes=int(arguments[0]),
        max_decoded_bytes=int(arguments[1]),
        max_records=int(arguments[2]),
    )
    data = Path("/input/request").read_bytes()
    request = primitive_document(data, MAX_REQUEST_BYTES)
    if request.get("protocol") != 1:
        raise ValueError("Unsupported analytical protocol")
    operation = request.get("operation")
    if operation == "build":
        result = _build(request, limits)
    elif operation == "inspect":
        result = _inspect(request, limits)
    else:
        raise ValueError("Unsupported analytical operation")
    result["request_sha256"] = sha256(data).hexdigest()
    output = canonical_json_bytes(result)
    if len(output) > MAX_RESULT_BYTES:
        raise ValueError("Analytical message limit")
    view = memoryview(output)
    while view:
        count = os.write(1, view)
        if count <= 0:
            raise ValueError("Analytical output unavailable")
        view = view[count:]
