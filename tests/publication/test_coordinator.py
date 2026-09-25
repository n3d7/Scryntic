"""F07 selection, visibility ordering, and end-to-end publication."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from scryntic.domain.raw import RawRecord
from scryntic.normalization.runner import RawRecordReader
from scryntic.publication.coordinator import (
    NoPublishableWork,
    PublicationError,
    Published,
    WaitingForNormalization,
)
from scryntic.publication.manifest import ManifestStorage
from tests.normalization.helpers import raw_record
from tests.publication.helpers import (
    FixedNormalizationReader,
    configured_coordinator,
    limits,
)


def test_offset_gaps_and_epoch_switch_keep_global_progress_and_epoch_chains(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5, 9), epochs=("epoch-a", "epoch-b", "epoch-a")
    )
    try:
        results = tuple(bundle.coordinator.publish_next() for _ in range(3))
        assert all(isinstance(result, Published) for result in results)
        published = tuple(result for result in results if isinstance(result, Published))
        assert [result.checkpoint.offset for result in published] == [2, 5, 9]
        assert [result.manifest.sequence for result in published] == [1, 1, 2]
        assert isinstance(bundle.coordinator.publish_next(), NoPublishableWork)
    finally:
        bundle.close()


def test_missing_first_outcome_waits_without_reservation(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    try:
        bundle.normalization_reader.outcomes.clear()
        result = bundle.coordinator.publish_next()
        assert isinstance(result, WaitingForNormalization)
        assert bundle.store.pending() is None
        assert bundle.store.status().checkpoint is None
    finally:
        bundle.close()


def test_publisher_contains_f05_failure_and_invalid_results(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)

    class Broken:
        def records_after(self, offset: int, *, limit: int) -> tuple[RawRecord, ...]:
            raise OSError("private detail")

    class Invalid:
        def records_after(self, offset: int, *, limit: int) -> list[RawRecord]:
            return []

    try:
        bundle.coordinator._raw_reader = Broken()
        with pytest.raises(PublicationError, match="raw records"):
            bundle.coordinator.publish_next()
        bundle.coordinator._raw_reader = cast(RawRecordReader, Invalid())
        with pytest.raises(PublicationError, match="reader result"):
            bundle.coordinator.publish_next()
    finally:
        bundle.close()


def test_reserved_inputs_are_reread_and_fingerprint_changes_fail_closed(
    tmp_path: Path,
) -> None:
    changed = False
    bundle = None

    def fault(stage: str) -> None:
        nonlocal changed
        if stage == "after_reservation_commit" and not changed:
            changed = True
            assert bundle is not None
            original = bundle.records[0]
            replacement = raw_record(
                offset=original.identity.offset, payload=b"not json"
            )
            bundle.raw_reader.records = (replacement,)

    bundle = configured_coordinator(tmp_path, fault=fault)
    try:
        with pytest.raises(PublicationError, match="reservation"):
            bundle.coordinator.publish_next()
        assert bundle.store.pending() is not None
        assert bundle.store.status().checkpoint is None
    finally:
        bundle.close()


def test_objects_partition_views_manifest_and_catalog_follow_durability_order(
    tmp_path: Path,
) -> None:
    observed: list[str] = []
    bundle = None

    def fault(stage: str) -> None:
        observed.append(stage)
        if bundle is None:
            return
        if stage == "after_prepare_commit":
            assert bundle.store.status().checkpoint is None
        if stage == "after_manifest_directory_fsync":
            assert bundle.store.status().checkpoint is None

    bundle = configured_coordinator(tmp_path, fault=fault)
    try:
        result = bundle.coordinator.publish_next()
        assert isinstance(result, Published)
        assert observed.index("after_raw_object") < observed.index(
            "after_prepare_commit"
        )
        assert observed.index("after_normalized_object") < observed.index(
            "after_prepare_commit"
        )
        assert observed.index("after_manifest_directory_fsync") < observed.index(
            "after_catalog_commit"
        )
        archive_root = bundle.root.state_dir / "archive"
        for descriptor in result.document.body.objects:
            assert (
                archive_root
                / "objects"
                / "sha256"
                / descriptor.sha256[:2]
                / f"{descriptor.sha256}.parquet"
            ).is_file()
        assert (
            ManifestStorage(bundle.root).read_exact(
                result.manifest, limits().max_manifest_bytes
            )
            == result.document_bytes
        )
    finally:
        bundle.close()


def test_partition_change_ends_reserved_prefix(tmp_path: Path) -> None:
    first = raw_record(offset=2)
    second = raw_record(offset=5)
    second = RawRecord(
        second.identity,
        replace(second.envelope, source="other", payload_limit=10_000),
    )
    normalized = FixedNormalizationReader((first, second))
    bundle = configured_coordinator(tmp_path, offsets=(2,), epochs=("epoch-a",))
    try:
        bundle.raw_reader.records = (first, second)
        bundle.coordinator._normalization_reader = normalized
        first_result = bundle.coordinator.publish_next()
        assert isinstance(first_result, Published)
        assert first_result.document.body.record_count == 1
    finally:
        bundle.close()
