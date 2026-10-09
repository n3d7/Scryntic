"""The registration point for application-owned forecasting adapters."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from scryntic.application.providers import ModelIdentity, ProviderDescriptor
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.jobs.admission import AdmissionPolicy, LoadingRequirements, ModelReview
from scryntic.model_worker.profile import FORECAST_CPU, SYNTHETIC_CPU, CPUProfile
from scryntic.models.adapters import ForecastAdapter, TimesFMAdapter
from scryntic.models.manifest import (
    MODEL_REPOSITORY,
    MODEL_REVISION,
    TIMESFM_ASSETS,
    ModelAssets,
)

POLICY_ID = "f22-offline-research-v1"
LICENSE_ID = "Apache-2.0"


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    name: str
    origin: str
    publisher: str
    revision: str
    profile: CPUProfile
    adapter: Callable[[], ForecastAdapter] | None
    algorithm: str | None = None
    assets: ModelAssets | None = None

    def review(self, runtime_inventory_sha256: str | None = None) -> ModelReview:
        real = self.adapter is not None
        if real and runtime_inventory_sha256 is None:
            raise ValueError("Model requires verified provisioned runtime identity")
        assets = self.assets
        if real != (assets is not None):
            raise ValueError("Model definition lacks reviewed asset metadata")
        hashes = (
            tuple(item[1] for item in assets.artifacts)
            + (
                assets.package_sha256,
                assets.runtime_lock_sha256,
                runtime_inventory_sha256 or "",
            )
            if assets is not None
            else (sha256(self.revision.encode("ascii")).hexdigest(),)
        )
        return ModelReview(
            descriptor=ProviderDescriptor(
                provider_id=self.name,
                model=ModelIdentity(
                    origin=self.origin,
                    publisher=self.publisher,
                    revision=self.revision,
                    license_id=LICENSE_ID,
                    usage_policy_id=POLICY_ID,
                    loading_requirements=("offline-safetensors-v1",)
                    if real
                    else ("builtin-json-only",),
                    artifact_sha256=hashes,
                ),
                capabilities=frozenset({"forecast"}),
                input_schemas=(EVOLVED_DATASET_SCHEMA,),
                max_rows=1024,
                max_horizon=24,
                execution="local",
            ),
            package_license=LICENSE_ID,
            code_license=LICENSE_ID,
            weight_license=LICENSE_ID,
            model_card=assets.model_card if assets is not None else "f20-fake-card-v1",
            terms=assets.terms
            if assets is not None
            else "f20-loopback-no-retention-v1",
            reviewed_on="2026-10-06",
            verified_artifacts=hashes,
            loading=LoadingRequirements(
                runtime="cpython-3.12-cpu-v1",
                format="safetensors",
                code="reviewed-adapter",
                max_memory_bytes=self.profile.memory,
            )
            if real
            else LoadingRequirements(),
        )


# Adding a compatible model changes its focused adapter and this definition only.
DEFINITIONS = (
    ModelDefinition(
        "fake-persistence",
        "scryntic:builtin-f20-fake",
        "scryntic",
        "f20-fixed-persistence-v1",
        SYNTHETIC_CPU,
        None,
        "persistence",
    ),
    ModelDefinition(
        "fake-trend",
        "scryntic:builtin-f20-fake",
        "scryntic",
        "f20-fixed-trend-v1",
        SYNTHETIC_CPU,
        None,
        "trend",
    ),
    ModelDefinition(
        "timesfm-2.5",
        "https://huggingface.co/" + MODEL_REPOSITORY,
        "google",
        MODEL_REVISION,
        FORECAST_CPU,
        TimesFMAdapter,
        assets=TIMESFM_ASSETS,
    ),
)


def definition(name: str) -> ModelDefinition:
    for item in DEFINITIONS:
        if item.name == name:
            return item
    raise ValueError("Unknown registered local model")


def policy(reviews: tuple[ModelReview, ...]) -> AdmissionPolicy:
    return AdmissionPolicy(
        policy_id=POLICY_ID,
        approved_reviews=tuple(r.sha256 for r in reviews),
        approved_licenses=frozenset({LICENSE_ID}),
        allowed_uses=frozenset({"forecast-research"}),
    )
