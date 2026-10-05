"""Application-owned primitive fixtures; no downloaded model or credentials."""

from hashlib import sha256
from typing import Literal

from scryntic.application.analysis import VerifiedDataset
from scryntic.application.providers import (
    ForecastRequest,
    ModelIdentity,
    ProviderDescriptor,
)
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.jobs.admission import AdmissionPolicy, ModelReview
from scryntic.jobs.contracts import JobRequest

NOW = 1_791_200_000_000_000_000
STEP = 60_000_000_000
LAST = 1_700_000_060_000_000_000


def review(
    execution: Literal["local", "remote"] = "local",
    *,
    algorithm: Literal["persistence", "trend"] = "persistence",
) -> ModelReview:
    from scryntic.jobs.admission import LoadingRequirements, ModelReview

    revision = "f20-fixed-" + algorithm + "-v1"
    artifact = sha256(revision.encode("ascii")).hexdigest()
    descriptor = ProviderDescriptor(
        provider_id="f20-fake-" + execution,
        model=ModelIdentity(
            origin="scryntic:builtin-f20-fake",
            publisher="scryntic",
            revision=revision,
            license_id="Apache-2.0",
            usage_policy_id="f20-research",
            loading_requirements=("builtin-json-only",),
            artifact_sha256=(artifact,),
        ),
        capabilities=frozenset({"forecast"}),
        input_schemas=(EVOLVED_DATASET_SCHEMA,),
        max_rows=1024,
        max_horizon=24,
        execution=execution,
    )
    return ModelReview(
        descriptor=descriptor,
        package_license="Apache-2.0",
        code_license="Apache-2.0",
        weight_license="Apache-2.0",
        model_card="f20-fake-card-v1",
        terms="f20-loopback-no-retention-v1",
        reviewed_on="2026-10-05",
        verified_artifacts=(artifact,),
        loading=LoadingRequirements(),
    )


def policy(model: ModelReview, *, remote: bool = False) -> AdmissionPolicy:
    from scryntic.jobs.admission import AdmissionPolicy

    return AdmissionPolicy(
        policy_id="f20-research",
        approved_reviews=(model.sha256,),
        approved_licenses=frozenset({"Apache-2.0"}),
        allowed_uses=frozenset({"forecast-research"}),
        remote_enabled=remote,
    )


def job(
    job_id: str = "job-a",
    *,
    execution: Literal["local", "remote"] = "local",
    remote: bool = False,
    algorithm: Literal["persistence", "trend"] = "persistence",
) -> JobRequest:
    from scryntic.jobs.contracts import ForecastInputs, JobRequest

    model = review(execution, algorithm=algorithm)
    dataset = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, 2)
    request = ForecastRequest(
        request_id=job_id,
        dataset=dataset,
        target="close",
        frequency_ns=STEP,
        horizon=2,
        allow_remote=remote,
    )
    return JobRequest(
        forecast=request,
        review=model,
        policy=policy(model, remote=remote),
        inputs=ForecastInputs(VerifiedDataset(dataset, LAST, STEP), (1.0, 2.0)),
        intended_use="forecast-research",
        submitted_ns=NOW,
        deadline_ns=NOW + 10_000_000_000,
    )
