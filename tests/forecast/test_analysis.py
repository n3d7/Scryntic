"""Provider output is validated before any F09 artifact is accepted."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.analysis import ForecastAnalysis, VerifiedDataset
from scryntic.application.providers import (
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
    ModelIdentity,
    ProviderDescriptor,
)
from scryntic.configuration.paths import Installation
from scryntic.dataset.snapshot import DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import SchemaRef, Version
from scryntic.forecast.artifact import ImmutableForecastStore

LAST_START_NS = 1_700_000_060_000_000_000
INTERVAL_NS = 60_000_000_000


class StubProvider:
    descriptor = ProviderDescriptor(
        provider_id="f09-unit-fake",
        model=ModelIdentity(
            origin="F09 in-process test fixture",
            publisher="scryntic",
            revision="f09-unit-v1",
            license_id="internal-test-only",
            usage_policy_id="f09-fake-only",
            loading_requirements=(),
        ),
        capabilities=frozenset({"forecast"}),
        input_schemas=(DATASET_SCHEMA,),
        max_rows=10,
        max_horizon=3,
        execution="local",
    )

    def __init__(self, defect: str | None = None) -> None:
        self.defect = defect

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        start = LAST_START_NS + INTERVAL_NS
        if self.defect == "shifted_origin":
            start += INTERVAL_NS
        points = tuple(
            ForecastPoint(
                start
                + index * INTERVAL_NS * (2 if self.defect == "wrong_spacing" else 1),
                1.0,
            )
            for index in range(request.horizon)
        )
        if self.defect == "short_horizon":
            points = points[:-1]
        if self.defect == "nonfinite":
            object.__setattr__(points[0], "value", float("nan"))
        return ForecastResult(
            "wrong-request" if self.defect == "wrong_request" else request.request_id,
            (
                DatasetRef("b" * 64, DATASET_SCHEMA, 2)
                if self.defect == "wrong_dataset"
                else DatasetRef("a" * 64, DATASET_SCHEMA, 3)
                if self.defect == "wrong_row_count"
                else request.dataset
            ),
            "wrong-provider"
            if self.defect == "wrong_provider"
            else self.descriptor.provider_id,
            "wrong-model"
            if self.defect == "wrong_model"
            else self.descriptor.model.revision,
            points,
        )


def _installation(tmp_path: Path) -> Installation:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    installation = Installation.workstation(home=home, environment={})
    for path in (
        installation.config_dir,
        installation.state_dir,
        installation.runtime_dir,
        installation.credential_dir,
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return installation


def _request() -> tuple[ForecastRequest, VerifiedDataset]:
    dataset = DatasetRef("a" * 64, DATASET_SCHEMA, 2)
    return (
        ForecastRequest(
            request_id="f09-unit-request",
            dataset=dataset,
            target="close",
            frequency_ns=INTERVAL_NS,
            horizon=2,
        ),
        VerifiedDataset(dataset, LAST_START_NS, INTERVAL_NS),
    )


def test_validated_artifact_binds_dataset_and_model(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    store = ImmutableForecastStore(installation)
    request, verified = _request()
    ref = asyncio.run(ForecastAnalysis(StubProvider(), store).run(request, verified))
    payload = store.read(ref)

    assert ref.dataset == request.dataset
    assert payload["dataset"]["manifest_sha256"] == request.dataset.manifest_sha256
    assert payload["provider"]["model"]["revision"] == "f09-unit-v1"
    assert [point["timestamp_ns"] for point in payload["result"]["points"]] == [
        LAST_START_NS + INTERVAL_NS,
        LAST_START_NS + 2 * INTERVAL_NS,
    ]


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_request",
        "wrong_dataset",
        "wrong_row_count",
        "wrong_provider",
        "wrong_model",
        "short_horizon",
        "wrong_spacing",
        "shifted_origin",
        "nonfinite",
    ],
)
def test_bad_provider_result_creates_no_artifact(tmp_path: Path, defect: str) -> None:
    installation = _installation(tmp_path)
    store = ImmutableForecastStore(installation)
    request, verified = _request()
    with pytest.raises((TypeError, ValueError)):
        asyncio.run(
            ForecastAnalysis(StubProvider(defect), store).run(request, verified)
        )
    assert not (installation.state_dir / "forecasts").exists()


@pytest.mark.parametrize("invalid", ["schema", "rows", "horizon", "frequency"])
def test_unsupported_request_creates_no_artifact(tmp_path: Path, invalid: str) -> None:
    installation = _installation(tmp_path)
    store = ImmutableForecastStore(installation)
    request, verified = _request()
    if invalid == "schema":
        dataset = DatasetRef("a" * 64, SchemaRef("other-dataset", Version(1, 0)), 2)
        request = replace(request, dataset=dataset)
        verified = replace(verified, reference=dataset)
    elif invalid == "rows":
        dataset = DatasetRef("a" * 64, DATASET_SCHEMA, 11)
        request = replace(request, dataset=dataset)
        verified = replace(verified, reference=dataset)
    elif invalid == "horizon":
        request = replace(request, horizon=4)
    else:
        request = replace(request, frequency_ns=INTERVAL_NS * 2)
    with pytest.raises((TypeError, ValueError)):
        asyncio.run(ForecastAnalysis(StubProvider(), store).run(request, verified))
    assert not (installation.state_dir / "forecasts").exists()
