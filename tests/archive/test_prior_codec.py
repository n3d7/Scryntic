"""Historical F07 bytes remain readable by the selected production codec."""

import asyncio
import json
from hashlib import sha256
from pathlib import Path

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.canonical import raw_projection
from scryntic.archive.raw_parquet import PARQUET_CODEC, ParquetRawArchive
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.domain.raw import IngestionId
from tests.archive.helpers import installation

FIXTURES = Path(__file__).parent / "fixtures"
PRIOR_HASH = "f7d43f0b6eededc7cdea5b8c4c656904595eb59d4eab1f9c79c650677eccc883"


def test_f07_raw_parquet_fixture_preserves_exact_binary_payload(tmp_path: Path) -> None:
    data = (FIXTURES / "f07-parquet-pyarrow-v1.parquet").read_bytes()
    metadata = json.loads((FIXTURES / "f07-parquet-pyarrow-v1.json").read_text())
    assert sha256(data).hexdigest() == PRIOR_HASH == metadata["sha256"]
    assert metadata["codec"] == PARQUET_CODEC
    root = installation(tmp_path)
    storage = ImmutableArchiveStorage(root)
    staging = storage.create_staging("prior-codec")
    staging.write_bytes(data)
    size, object_hash = storage.prepare_staging(staging)
    assert size == metadata["encoded_bytes"] == 5529
    storage.install_staging(staging, object_hash)
    # The decoder is chosen in local code, never imported from fixture metadata.
    archive = ParquetRawArchive(root)
    record = asyncio.run(
        archive.read(
            RawRecordRef(PRIOR_HASH, 0, IngestionId("collector-a", "epoch-a", 7)),
            ArchiveLimits(4, 100000, 100000),
        )
    )
    assert record.envelope.payload == b'\x00\xffprior-codec\n{"exact":true}'
    assert raw_projection(record) == metadata["records"][0]
    assert storage.object_path(PRIOR_HASH).read_bytes() == data
