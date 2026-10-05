"""F20 obtains finalized F18 data through the restricted decoder and pins lineage."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.providers import ForecastRequest
from scryntic.dataset.service import F18_RECIPE_SCHEMA
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.imports.catalog import ImportCatalog
from scryntic.jobs.fake import LocalFakeProvider
from scryntic.jobs.inputs import F18Inputs
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobStore
from tests.dataset.test_imported_recipes import _publish, _service
from tests.dataset.test_recipes import _received
from tests.dataset.test_selection import _source
from tests.jobs.helpers import NOW, policy, review
from tests.normalization.helpers import DEFAULT_RECEIPT, installation


@pytest.mark.parametrize("defect", ["none", "gap", "unfinalized", "frequency"])
def test_real_f18_input_and_artifact_preserve_dataset_lineage(
    tmp_path: Path, defect: str
) -> None:
    first = _received(_source(1, finalized=True), 10)
    assert first.value.semantics is not None
    key = first.value.semantics.key
    second = _received(
        _source(
            2,
            close="100.20",
            finalized=defect != "unfinalized",
            start_ns=key.start_ns + key.interval_ns * (2 if defect == "gap" else 1),
        ),
        20,
    )
    data, incoming = _publish(tmp_path, (first, second))
    target = installation(tmp_path / "workstation")
    with ImportCatalog(target) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        datasets = _service(catalog, target)
        dataset = datasets.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        ).dataset
        request = ForecastRequest(
            request_id="f20-real-f18",
            dataset=dataset,
            target="close",
            covariates=(),
            frequency_ns=key.interval_ns * (2 if defect == "frequency" else 1),
            horizon=2,
        )
        inputs = F18Inputs(datasets)
        if defect != "none":
            with pytest.raises(ValueError):
                inputs.read(request)
            return
        original_pins = datasets.pins(dataset)
        original_manifest = datasets.read_manifest(dataset)
        with JobStore(target, clock=lambda: NOW) as store:
            model = review()
            artifacts = ImmutableForecastStore(target)
            service = JobService(
                store, (LocalFakeProvider(model),), policy(model), artifacts, inputs
            )
            submitted = service.submit(
                request,
                model.descriptor.provider_id,
                intended_use="forecast-research",
                deadline_ns=NOW + 10_000_000_000,
            )
            result = asyncio.run(service.run(request.request_id))
            assert result.state.value == "succeeded"
            reference = service.result(request.request_id)
            assert reference.dataset == dataset
            assert (
                artifacts.read(reference)["job"]["request_sha256"]
                == submitted.job.sha256
            )
            assert datasets.pins(dataset) == original_pins
            assert datasets.read_manifest(dataset) == original_manifest
            assert imported.manifest_hash in original_pins["publication_manifests"]
            unsupported = replace(request, target="volume")
            with pytest.raises(ValueError):
                inputs.read(unsupported)
            asyncio.run(service.aclose())
