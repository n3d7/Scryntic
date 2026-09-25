"""Subprocess driver that exits at one named F07 durability boundary."""

import os
import sys
from pathlib import Path

from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.publication.coordinator import PublicationCoordinator
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.sqlite_store import PublicationStore
from tests.archive.helpers import installation
from tests.normalization.helpers import FixedRawReader, raw_record
from tests.publication.helpers import FixedNormalizationReader, limits


def main() -> int:
    root = installation(Path(sys.argv[1]))
    mode = sys.argv[2]
    boundary = sys.argv[3] if len(sys.argv) > 3 else ""
    record = raw_record(offset=2)
    raw_reader = FixedRawReader((record,))
    normalization_reader = FixedNormalizationReader((record,))

    def fault(stage: str) -> None:
        if mode == "publish" and stage == boundary:
            os._exit(73)

    with PublicationStore(root, producer="collector-a") as store:
        coordinator = PublicationCoordinator(
            raw_reader,
            normalization_reader,
            store,
            ParquetRawArchive(root),
            NormalizedParquetArchive(root),
            ManifestStorage(root, fault=fault),
            root,
            limits(),
            fault=fault,
        )
        if mode == "publish":
            coordinator.publish_next()
        else:
            coordinator.recover()
            if store.status().checkpoint != record.identity:
                return 2
            if len(store.catalog_page(None, 10)) != 1:
                return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
