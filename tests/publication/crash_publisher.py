"""Subprocess driver that exits at one named F07 durability boundary."""

import os
import sys
from pathlib import Path

from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.domain.raw import RawRecord
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
    scenario = sys.argv[4] if len(sys.argv) > 4 else "genesis"
    records: tuple[RawRecord, ...] = (raw_record(offset=2),)
    if scenario != "genesis":
        records += (
            raw_record(
                offset=5, epoch="epoch-a" if scenario == "continuation" else "epoch-b"
            ),
        )
    raw_reader = FixedRawReader(records)
    normalization_reader = FixedNormalizationReader(records)

    armed = False

    def fault(stage: str) -> None:
        if armed and mode == "publish" and stage == boundary:
            os._exit(73)

    with PublicationStore(root, producer="collector-a", fault=fault) as store:
        coordinator = PublicationCoordinator(
            raw_reader,
            normalization_reader,
            store,
            ParquetRawArchive(root, fault=lambda stage: fault(f"raw_{stage}")),
            NormalizedParquetArchive(
                root, fault=lambda stage: fault(f"normalized_{stage}")
            ),
            ManifestStorage(root, fault=fault),
            root,
            limits(1),
            fault=fault,
        )
        if mode == "publish":
            if scenario != "genesis":
                coordinator.publish_next()
            armed = True
            coordinator.publish_next()
        else:
            coordinator.recover()
            if store.status().checkpoint != records[-1].identity:
                coordinator.publish_next()
            if store.status().checkpoint != records[-1].identity:
                return 2
            if len(store.catalog_page(None, 10)) != len(records):
                return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
