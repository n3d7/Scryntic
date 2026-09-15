"""Conformance examples exercise owned checks through replaceable test providers."""

import asyncio
from dataclasses import replace
from typing import cast

import pytest

from scryntic.application.archive import ArchiveLimits, RawSegment
from scryntic.application.dto import BuildDatasetRequest, CollectRequest
from scryntic.application.providers import (
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
    ModelIdentity,
    ProviderDescriptor,
)
from scryntic.application.sources import (
    SourceCapability,
    SourceDescriptor,
    SourceOperation,
    StreamRequest,
)
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import EntityId, InstrumentId, SchemaRef, Version
from scryntic.domain.market import CANDLE_SCHEMA


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id="fake",
        capabilities=frozenset({"forecast"}),
        max_rows=100,
        max_horizon=4,
        execution="local",
        input_schemas=(CANDLE_SCHEMA,),
        model=ModelIdentity(
            origin="test-fixture",
            publisher="scryntic",
            revision="r1",
            license_id="Apache-2.0",
            usage_policy_id="tests-only",
            loading_requirements=("stdlib",),
        ),
    )


def request() -> ForecastRequest:
    return ForecastRequest(
        request_id="job-1",
        dataset=DatasetRef("a" * 64, CANDLE_SCHEMA, 3),
        target="close",
        frequency_ns=60_000_000_000,
        horizon=2,
    )


def source_descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        "fake",
        "1.0",
        (
            SourceCapability(
                CANDLE_SCHEMA,
                "instrument",
                "spot",
                frozenset({SourceOperation.STREAM}),
                "none",
                "backfill",
            ),
            SourceCapability(
                SchemaRef("news", Version(1, 0)),
                "document",
                None,
                frozenset({SourceOperation.HISTORY}),
                "none",
                "coverage-loss",
            ),
        ),
        max_payload_bytes=1024,
        max_page_records=10,
    )


def test_source_negotiation_is_per_family_category_and_operation() -> None:
    descriptor = source_descriptor()
    spot = InstrumentId("fake", "spot", "BTC-USDT")
    descriptor.require(CANDLE_SCHEMA, SourceOperation.STREAM, spot)
    descriptor.require(
        SchemaRef("news", Version(1, 0)),
        SourceOperation.HISTORY,
        EntityId("document", "publisher", "news-1"),
    )
    for schema, operation, subject in (
        (CANDLE_SCHEMA, SourceOperation.HISTORY, spot),
        (CANDLE_SCHEMA, SourceOperation.STREAM, replace(spot, category="perpetual")),
        (SchemaRef("candle", Version(2, 0)), SourceOperation.STREAM, spot),
    ):
        with pytest.raises(ValueError, match="Unsupported source capability"):
            descriptor.require(schema, operation, subject)


def test_provider_limits_capabilities_and_remote_intent_fail_explicitly() -> None:
    local = descriptor()
    local.require(request())
    for provider, job in (
        (replace(local, capabilities=frozenset()), request()),
        (local, replace(request(), horizon=5)),
        (local, replace(request(), dataset=replace(request().dataset, row_count=101))),
        (replace(local, execution="remote"), request()),
    ):
        with pytest.raises(ValueError, match="Unsupported provider request"):
            provider.require(job)
    replace(local, execution="remote").require(replace(request(), allow_remote=True))
    with pytest.raises(TypeError):
        replace(local, capabilities=cast(frozenset[str], {"forecast"}))


def test_forecast_result_correlation_and_bounds() -> None:
    result = ForecastResult(
        "job-1",
        request().dataset,
        "fake",
        "r1",
        (
            ForecastPoint(60_000_000_000, 1.0),
            ForecastPoint(120_000_000_000, 2.0),
        ),
    )
    result.validate_for(request(), descriptor())
    for bad in (
        replace(result, request_id="other"),
        replace(result, points=()),
        replace(result, model_revision="mutable-latest"),
        replace(result, dataset=replace(result.dataset, manifest_sha256="b" * 64)),
    ):
        with pytest.raises(ValueError):
            bad.validate_for(request(), descriptor())
    with pytest.raises(ValueError):
        ForecastPoint(0, float("nan"))
    with pytest.raises(ValueError):
        replace(result, points=tuple(reversed(result.points)))


def test_dataset_and_archive_refs_are_hashes_not_paths_or_codecs_to_load() -> None:
    with pytest.raises(ValueError):
        replace(request().dataset, manifest_sha256="../../etc/passwd")
    segment = RawSegment(
        "b" * 64, SchemaRef("raw", Version(1, 0)), "opaque-codec", 50, 70, 2
    )
    segment.require_within(ArchiveLimits(2, 50, 70))
    with pytest.raises(ValueError):
        segment.require_within(ArchiveLimits(2, 50, 69))
    dataset_request = BuildDatasetRequest(
        ("a" * 64,), SchemaRef("recipe", Version(1, 0))
    )
    assert dataset_request.input_manifests == ("a" * 64,)
    assert (
        CollectRequest("fake", StreamRequest(CANDLE_SCHEMA, None)).source_id == "fake"
    )


def test_fake_contract_conformance_end_to_end_without_io() -> None:
    from tests.contracts.fakes import FakeClock, FakeProvider, FakeSource, exercise

    values = asyncio.run(exercise(FakeSource(), FakeClock(), FakeProvider(), request()))
    assert values == (b"abc", (1.0, 2.0))


def test_provider_rejects_unsupported_input_schema_and_covariates() -> None:
    for job in (
        replace(request(), covariates=("volume",)),
        replace(
            request(),
            dataset=replace(request().dataset, schema=SchemaRef("news", Version(1, 0))),
        ),
    ):
        with pytest.raises(ValueError, match="Unsupported provider request"):
            descriptor().require(job)


def test_history_pages_and_archive_references_preserve_opaque_identity() -> None:
    from scryntic.application.archive import RawRecordRef
    from scryntic.application.sources import HistoryRequest, RawPage
    from tests.contracts.test_domain import candle, envelope

    history = HistoryRequest(
        StreamRequest(CANDLE_SCHEMA, candle().key.instrument),
        0,
        100,
        1,
        b"opaque=cursor",
    )
    page = RawPage((envelope(),), history.cursor, record_limit=1)
    assert page.next_cursor == b"opaque=cursor"
    with pytest.raises(ValueError):
        RawPage((envelope(), envelope()), None, record_limit=1)
    with pytest.raises(ValueError):
        replace(history, end_ns=0)
    ref = RawRecordRef("b" * 64, 0, candle().raw_record)
    assert ref.identity.offset == 1
