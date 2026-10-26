"""A bounded full snapshot is verified once without widening page inspection."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.domain.dataset import DatasetRef
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError, ImportLimits
from tests.dataset.test_protocol import row
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation


def test_real_scan_preserves_inspection_and_reopen(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        ref = service.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        ).dataset
        assert service.scan(ref) == service.inspect(ref)
        with pytest.raises(ValueError, match="row limit"):
            service.inspect(ref, limit=129)


@pytest.mark.parametrize("count", [0, 1, 129, 10000])
def test_scan_boundary_checks_full_cardinality_and_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    root = installation(tmp_path)
    reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, count)
    expected = [row() for _ in range(count)]
    with ImportCatalog(root) as catalog:
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        monkeypatch.setattr(
            service,
            "read_manifest",
            lambda _: {"parquet": {"sha256": "b" * 64, "encoded_bytes": 4}},
        )
        monkeypatch.setattr(service._storage, "read_artifact", lambda *a, **kw: b"PAR1")
        requests = []

        def launch(request: dict[str, Any], *args: Any) -> dict[str, Any]:
            requests.append(request)
            return {"request_sha256": "c" * 64, "rows": expected}

        monkeypatch.setattr(service, "_launch", launch)
        assert service.scan(reference) == expected
        assert len(requests) == 1
        assert requests[0]["operation"] == "scan"
        assert requests[0]["offset"] == 0
        assert requests[0]["limit"] == max(1, count)
        expected.append(row())
        with pytest.raises(ImportError, match="inspection response"):
            service.scan(reference)


def test_scan_over_ceiling_fails_before_input_access(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with ImportCatalog(root) as catalog:
        service = DatasetService(
            catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
        )
        reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, 10001)
        with pytest.raises(ValueError, match="[Ss]can.*limit"):
            service.scan(reference)


def test_native_scan_validates_lineage_beyond_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scryntic.dataset import worker

    values = [row() for _ in range(257)]
    table = pa.Table.from_pylist(values)
    output = pa.BufferOutputStream()
    pq.write_table(table, output)
    file = pq.ParquetFile(pa.BufferReader(output.getvalue()))
    lineage = [
        {
            "semantic_revision": r["semantic_revision"],
            "selected": {"identity": {"offset": r["offset"]}},
        }
        for r in values
    ]
    manifest = {"rows": lineage}
    monkeypatch.setattr(worker, "_inspection_file", lambda *a: (manifest, file))
    request = {"operation": "scan", "row_count": 257, "offset": 0, "limit": 257}
    assert worker._inspect(request, ImportLimits())["rows"] == values
    usage: dict[str, int] = {}
    worker._inspect(request, ImportLimits(), usage=usage)
    assert usage["rows_considered"] == 257
    # Decoded validity buffers and batch boundaries differ from the original
    # in-memory Table; accounting is actual returned batches, not file size.
    assert usage["decoded_bytes"] == sum(
        batch.nbytes for batch in file.iter_batches(batch_size=128, use_threads=False)
    )
    broken = deepcopy(lineage)
    broken[256]["semantic_revision"] = "sha256:" + "f" * 64
    manifest["rows"] = broken
    limits = ImportLimits()
    with pytest.raises(ValueError, match="lineage mismatch"):
        worker._inspect(request, limits)
