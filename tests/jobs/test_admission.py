"""Unknown metadata/loading requirements cannot authorize a model."""

from dataclasses import replace
from typing import Any

import pytest

from tests.jobs.helpers import job, policy, review


def test_exact_review_use_and_license_approval_admits_builtin_only() -> None:
    value = job()
    value.review.admit(value.policy, value.forecast, value.intended_use)
    assert value.review.sha256 in value.policy.approved_reviews


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin", "unknown"),
        ("origin", " UNKNOWN "),
        ("revision", None),
        ("revision", "main"),
        ("publisher", None),
        ("license_id", None),
        ("license_id", "unknown"),
        ("usage_policy_id", None),
        ("artifact_sha256", ()),
        ("loading_requirements", ("trust-remote-code",)),
    ],
)
def test_model_metadata_rejection_is_fail_closed(field: str, value: object) -> None:
    model = review()
    changes: dict[str, Any] = {field: value}
    identity = replace(model.descriptor.model, **changes)
    changed = replace(model, descriptor=replace(model.descriptor, model=identity))
    approved = policy(changed)
    request = job().forecast
    with pytest.raises(ValueError):
        changed.admit(approved, request, "forecast-research")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime", "unknown"),
        ("format", "pickle"),
        ("code", "downloaded"),
        ("network", True),
        ("downloads", True),
        ("device", "gpu"),
        ("max_memory_bytes", 0),
        ("max_memory_bytes", 1 << 40),
    ],
)
def test_unsafe_loading_is_rejected_even_with_exact_review_grant(
    field: str, value: object
) -> None:
    model = review()
    changes: dict[str, Any] = {field: value}
    changed = replace(model, loading=replace(model.loading, **changes))
    approved = policy(changed)
    request = job().forecast
    with pytest.raises(ValueError):
        changed.admit(approved, request, "forecast-research")


@pytest.mark.parametrize(
    "defect", ["review", "license", "use", "policy", "artifacts", "date"]
)
def test_explicit_grants_and_review_evidence_are_required(defect: str) -> None:
    value = job()
    model, approved = value.review, value.policy
    if defect == "review":
        approved = replace(approved, approved_reviews=())
    elif defect == "license":
        approved = replace(approved, approved_licenses=frozenset())
    elif defect == "use":
        approved = replace(approved, allowed_uses=frozenset())
    elif defect == "policy":
        approved = replace(approved, policy_id="other-policy")
    elif defect == "artifacts":
        model = replace(model, verified_artifacts=("b" * 64,))
        approved = policy(model)
    else:
        model = replace(model, reviewed_on="unknown")
        approved = policy(model)
    with pytest.raises(ValueError):
        model.admit(approved, value.forecast, value.intended_use)


@pytest.mark.parametrize(
    "policy_enabled,request_enabled", [(False, False), (False, True), (True, False)]
)
def test_remote_requires_two_explicit_authorizations(
    policy_enabled: bool, request_enabled: bool
) -> None:
    model = review("remote")
    approved = policy(model, remote=policy_enabled)
    request = replace(job().forecast, allow_remote=request_enabled)
    with pytest.raises(ValueError):
        model.admit(approved, request, "forecast-research")


def test_remote_is_admitted_only_when_both_authorizations_are_present() -> None:
    value = job(execution="remote", remote=True)
    value.review.admit(value.policy, value.forecast, value.intended_use)
