"""Deterministic synthetic fixture preparation, separate from measured jobs."""

from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import MAX_SCAN_ROWS
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA, schema_projection
from scryntic.domain.validation import integer


def synthetic_rows(*, count: int, gaps: bool) -> list[dict[str, Any]]:
    integer(count, 0)
    if count > MAX_SCAN_ROWS or type(gaps) is not bool:
        raise ValueError("Unregistered fixture size")
    rows = []
    for i in range(count):
        start = 1577836800 * 10**9 + (i + (i // 211 if gaps else 0)) * 3600 * 10**9
        if gaps and i % 997 == 0:
            start += 1
        close = Decimal(100 + i % 23).quantize(Decimal("0.000000000000000001"))
        revision = sha256(str(i).encode()).hexdigest()
        rows.append(
            {
                "venue": "synthetic",
                "category": "linear",
                "symbol": "BTCUSDT",
                "volume_unit": "base",
                "producer": "pi05",
                "epoch": "synthetic-2020",
                "manifest_sha256": "a" * 64,
                "raw_object_sha256": "b" * 64,
                "normalized_object_sha256": "c" * 64,
                "raw_sha256": revision,
                "semantic_revision": "sha256:" + revision,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": close,
                "start_ns": start,
                "interval_ns": 3600 * 10**9,
                "offset": i + 1,
                "input_ordinal": i,
                "receipt_wall_time_ns": start + 3600 * 10**9 + 1,
                "normalized_at_ns": start + 3600 * 10**9 + 2,
                "source_time_value": start,
                "source_time_unit": "ns",
                "publication_time_value": None,
                "publication_time_unit": None,
                "finalized": not (gaps and i % 97 == 0),
                "quality_flags": ["synthetic-gap"] if gaps and i % 131 == 0 else [],
                "lag_close": close if i else None,
                "label_close": None,
                "label_end_ns": None,
            }
        )
    return rows


def prepare_fixture(path: Path, *, count: int, gaps: bool) -> dict[str, Any]:
    # Constructed synthetic values only; no imported native input in a coordinator.
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from scryntic.dataset.worker import _primitive_row, table_schema

    rows = synthetic_rows(count=count, gaps=gaps)
    table = pa.Table.from_pylist(rows, schema=table_schema(True))
    output = pa.BufferOutputStream()
    pq.write_table(table, output, compression="NONE", row_group_size=max(1, count))
    parquet = output.getvalue().to_pybytes()
    manifest: dict[str, Any] = {
        "rows": [
            {
                "semantic_revision": r["semantic_revision"],
                "selected": {"identity": {"offset": r["offset"]}},
            }
            for r in rows
        ],
        "parquet": {
            "sha256": sha256(parquet).hexdigest(),
            "encoded_bytes": len(parquet),
        },
    }
    data = canonical_json_bytes(manifest)
    fixture = {
        "schema": "scryntic.pi05.synthetic.v1",
        "row_count": count,
        "gaps": gaps,
        "manifest_sha256": sha256(data).hexdigest(),
        "parquet_sha256": sha256(parquet).hexdigest(),
        "encoded_bytes": len(data) + len(parquet),
        "decoded_bytes": table.nbytes,
        "dataset_schema": schema_projection(EVOLVED_DATASET_SCHEMA),
        "expected_rows": [_primitive_row(r) for r in table.to_pylist()],
    }
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, value in (
        ("manifest.json", data),
        ("dataset.parquet", parquet),
        ("fixture.json", canonical_json_bytes(fixture)),
    ):
        with (path / name).open("xb") as stream:
            stream.write(value)
        (path / name).chmod(0o400)
    return fixture
