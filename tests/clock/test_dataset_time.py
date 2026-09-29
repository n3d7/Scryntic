from pathlib import Path

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.dataset.selection import SelectionError
from scryntic.dataset.snapshot import (
    AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
    CANDLE_RECIPE_SCHEMA,
    DatasetBuilder,
    DatasetBuildError,
    DatasetReader,
)
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.publication.coordinator import Published
from scryntic.publication.reader import PublicationReader
from tests.publication.helpers import CoordinatorBundle, configured_coordinator, limits


def receipt(wall: int, *, radius: int = 5, healthy: bool = True) -> ClockSample:
    quality = (
        TimeQuality("clock-epoch", "healthy", 0, radius, 0)
        if healthy
        else TimeQuality("clock-epoch", "unknown")
    )
    return ClockSample(wall, 10, "host-session", quality)


def build_fixture(
    tmp_path: Path, receipts: tuple[ClockSample, ...]
) -> tuple[CoordinatorBundle, str, DatasetBuilder]:
    bundle = configured_coordinator(
        tmp_path, offsets=(2, 5), epochs=("a", "a"), receipts=receipts
    )
    published = bundle.coordinator.publish_next()
    assert isinstance(published, Published)
    builder = DatasetBuilder(
        PublicationReader(bundle.store, bundle.root, limits()),
        bundle.root,
        code_revision="f12-test",
        dependency_lock_sha256="a" * 64,
    )
    return bundle, published.manifest.manifest_hash, builder


def test_strict_snapshot_excludes_unknown_receipt_but_preserves_raw_manifest(
    tmp_path: Path,
) -> None:
    base = 1_700_000_001_000_000_000
    bundle, manifest, builder = build_fixture(
        tmp_path, (receipt(base), receipt(base + 10, healthy=False))
    )
    try:
        request = BuildDatasetRequest(
            (manifest,),
            AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
            as_observed_cutoff=receipt(base + 100),
        )
        result = builder.build(request)
        document = DatasetReader(bundle.root).read_manifest(result.dataset)
        assert result.dataset.row_count == 1
        assert document["timing_policy"] == "strict-as-observed"
        assert document["as_observed_cutoff"]["session_id"] == "host-session"
        assert document["as_observed_cutoff"]["monotonic_ns"] == 10
        assert document["exclusions"] == [
            {
                "producer": "collector-a",
                "epoch": "a",
                "offset": 5,
                "reason": "not_proven_before_cutoff",
            }
        ]
        assert document["time_quality"] == ["healthy", "unknown"]
        assert (
            len(document["inputs"]) == 1
        )  # Entire validated immutable input manifest retained.
    finally:
        bundle.close()


def test_strict_snapshot_defers_when_uncertainty_overlaps_cutoff(
    tmp_path: Path,
) -> None:
    base = 1_700_000_001_000_000_000
    bundle, manifest, builder = build_fixture(
        tmp_path, (receipt(base + 4), receipt(base + 5))
    )
    try:
        with pytest.raises(DatasetBuildError) as error:
            builder.build(
                BuildDatasetRequest(
                    (manifest,),
                    AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
                    as_observed_cutoff=receipt(base + 9),
                )
            )
        assert isinstance(error.value.__cause__, SelectionError)
    finally:
        bundle.close()


def test_untrusted_cutoff_or_wrong_recipe_cannot_request_strict_selection(
    tmp_path: Path,
) -> None:
    base = 1_700_000_001_000_000_000
    bundle, manifest, builder = build_fixture(tmp_path, (receipt(base), receipt(base)))
    try:
        for request in (
            BuildDatasetRequest(
                (manifest,),
                AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
                as_observed_cutoff=receipt(base + 100, healthy=False),
            ),
            BuildDatasetRequest((manifest,), AS_OBSERVED_CANDLE_RECIPE_SCHEMA),
            BuildDatasetRequest(
                (manifest,),
                CANDLE_RECIPE_SCHEMA,
                as_observed_cutoff=receipt(base + 100),
            ),
        ):
            with pytest.raises(DatasetBuildError):
                builder.build(request)
    finally:
        bundle.close()


def test_historical_mode_keeps_flagged_records_without_claiming_observation_timing(
    tmp_path: Path,
) -> None:
    base = 1_700_000_001_000_000_000
    bundle, manifest, builder = build_fixture(
        tmp_path, (receipt(base, healthy=False), receipt(base + 5, healthy=False))
    )
    try:
        result = builder.build(BuildDatasetRequest((manifest,), CANDLE_RECIPE_SCHEMA))
        document = DatasetReader(bundle.root).read_manifest(result.dataset)
        assert document["timing_policy"] == "historical-reconstruction"
        assert document["time_quality"] == ["unknown"]
        assert result.dataset.row_count == 1
    finally:
        bundle.close()
