"""Closed canonical JSON, bounded before parsing; no vendor objects cross jobs."""

import json
from hashlib import sha256
from typing import Any

from scryntic.application.analysis import VerifiedDataset
from scryntic.application.providers import (
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
    ModelIdentity,
    ProviderDescriptor,
)
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import SchemaRef, Version
from scryntic.jobs.admission import (
    AdmissionPolicy,
    LoadingRequirements,
    ModelReview,
    projection,
)
from scryntic.jobs.contracts import (
    MAX_JOB_BYTES,
    MAX_RESPONSE_BYTES,
    RESPONSE_SCHEMA,
    ForecastInputs,
    JobAttempt,
    JobRequest,
)


def _object(value: Any, names: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError("Invalid or unknown contract fields")
    return value


def _array(value: Any, maximum: int) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError("Contract array exceeds bounds")
    return value


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate contract field")
        result[key] = value
    return result


def _constant(_value: str) -> object:
    raise ValueError("Nonfinite JSON constant")


def _document(data: bytes, maximum: int) -> dict[str, Any]:
    if type(data) is not bytes or not 0 < len(data) <= maximum:
        raise ValueError("Contract byte limit")
    try:
        document = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        if type(document) is not dict or canonical_json_bytes(document) != data:
            raise ValueError("Noncanonical contract")
        return document
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Invalid bounded canonical JSON contract") from None


def _version(value: Any) -> Version:
    data = _object(value, "major minor")
    return Version(data["major"], data["minor"])


def _schema(value: Any) -> SchemaRef:
    data = _object(value, "name version")
    return SchemaRef(data["name"], _version(data["version"]))


def _dataset(value: Any) -> DatasetRef:
    data = _object(value, "manifest_sha256 schema row_count")
    return DatasetRef(
        data["manifest_sha256"], _schema(data["schema"]), data["row_count"]
    )


def _request(value: Any) -> ForecastRequest:
    data = _object(
        value, "request_id dataset target frequency_ns horizon covariates allow_remote"
    )
    return ForecastRequest(
        request_id=data["request_id"],
        dataset=_dataset(data["dataset"]),
        target=data["target"],
        frequency_ns=data["frequency_ns"],
        horizon=data["horizon"],
        covariates=tuple(_array(data["covariates"], 256)),
        allow_remote=data["allow_remote"],
    )


def _descriptor(value: Any) -> ProviderDescriptor:
    data = _object(
        value,
        "provider_id model capabilities input_schemas max_rows max_horizon execution contract_version",
    )
    model = _object(
        data["model"],
        "origin publisher revision license_id usage_policy_id loading_requirements artifact_sha256",
    )
    identity = ModelIdentity(
        origin=model["origin"],
        publisher=model["publisher"],
        revision=model["revision"],
        license_id=model["license_id"],
        usage_policy_id=model["usage_policy_id"],
        loading_requirements=tuple(_array(model["loading_requirements"], 64)),
        artifact_sha256=tuple(_array(model["artifact_sha256"], 64)),
    )
    return ProviderDescriptor(
        provider_id=data["provider_id"],
        model=identity,
        capabilities=frozenset(_array(data["capabilities"], 64)),
        input_schemas=tuple(
            _schema(item) for item in _array(data["input_schemas"], 64)
        ),
        max_rows=data["max_rows"],
        max_horizon=data["max_horizon"],
        execution=data["execution"],
        contract_version=_version(data["contract_version"]),
    )


def _review(value: Any) -> ModelReview:
    data = _object(
        value,
        "descriptor package_license code_license weight_license model_card terms reviewed_on verified_artifacts loading",
    )
    loading = _object(
        data["loading"], "runtime format code network downloads device max_memory_bytes"
    )
    return ModelReview(
        descriptor=_descriptor(data["descriptor"]),
        package_license=data["package_license"],
        code_license=data["code_license"],
        weight_license=data["weight_license"],
        model_card=data["model_card"],
        terms=data["terms"],
        reviewed_on=data["reviewed_on"],
        verified_artifacts=tuple(_array(data["verified_artifacts"], 64)),
        loading=LoadingRequirements(**loading),
    )


def _policy(value: Any) -> AdmissionPolicy:
    data = _object(
        value,
        "policy_id approved_reviews approved_licenses allowed_uses remote_enabled allowed_input_fields",
    )
    return AdmissionPolicy(
        policy_id=data["policy_id"],
        approved_reviews=tuple(_array(data["approved_reviews"], 64)),
        approved_licenses=frozenset(_array(data["approved_licenses"], 64)),
        allowed_uses=frozenset(_array(data["allowed_uses"], 64)),
        remote_enabled=data["remote_enabled"],
        allowed_input_fields=frozenset(_array(data["allowed_input_fields"], 64)),
    )


def _number(value: Any) -> float:
    if type(value) is not str or len(value) > 64:
        raise ValueError("Invalid bounded float representation")
    number = float(value)
    if repr(number) != value:
        raise ValueError("Noncanonical float representation")
    return number


def _job(value: Any) -> JobRequest:
    data = _object(
        value,
        "forecast review policy inputs intended_use submitted_ns deadline_ns seed schema",
    )
    inputs = _object(data["inputs"], "verified closes")
    verified = _object(inputs["verified"], "reference last_start_ns interval_ns")
    return JobRequest(
        forecast=_request(data["forecast"]),
        review=_review(data["review"]),
        policy=_policy(data["policy"]),
        inputs=ForecastInputs(
            VerifiedDataset(
                _dataset(verified["reference"]),
                verified["last_start_ns"],
                verified["interval_ns"],
            ),
            tuple(_number(item) for item in _array(inputs["closes"], 2)),
        ),
        intended_use=data["intended_use"],
        submitted_ns=data["submitted_ns"],
        deadline_ns=data["deadline_ns"],
        seed=data["seed"],
        schema=_schema(data["schema"]),
    )


def encode_job(job: JobRequest) -> bytes:
    job.__post_init__()
    data = canonical_json_bytes(projection(job))
    if len(data) > MAX_JOB_BYTES:
        raise ValueError("Job byte limit")
    return data


def decode_job(data: bytes) -> JobRequest:
    job = _job(_document(data, MAX_JOB_BYTES))
    if encode_job(job) != data:
        raise ValueError("Job contract mismatch")
    return job


def encode_attempt(attempt: JobAttempt) -> bytes:
    attempt.__post_init__()
    data = canonical_json_bytes(projection(attempt))
    if len(data) > MAX_JOB_BYTES:
        raise ValueError("Attempt byte limit")
    return data


def decode_attempt(data: bytes) -> JobAttempt:
    document = _object(_document(data, MAX_JOB_BYTES), "job number token")
    attempt = JobAttempt(_job(document["job"]), document["number"], document["token"])
    if encode_attempt(attempt) != data:
        raise ValueError("Attempt contract mismatch")
    return attempt


def encode_response(attempt: JobAttempt, result: ForecastResult) -> bytes:
    document = {
        "schema": projection(RESPONSE_SCHEMA),
        "job_sha256": attempt.job.sha256,
        "attempt": attempt.number,
        "token": attempt.token,
        "request_sha256": sha256(encode_attempt(attempt)).hexdigest(),
        "result": projection(result),
    }
    data = canonical_json_bytes(document)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("Response byte limit")
    return data


def decode_response(data: bytes, attempt: JobAttempt) -> ForecastResult:
    document = _object(
        _document(data, MAX_RESPONSE_BYTES),
        "schema job_sha256 attempt token request_sha256 result",
    )
    if (
        _schema(document["schema"]) != RESPONSE_SCHEMA
        or document["job_sha256"] != attempt.job.sha256
        or type(document["attempt"]) is not int
        or document["attempt"] != attempt.number
        or document["token"] != attempt.token
        or document["request_sha256"] != sha256(encode_attempt(attempt)).hexdigest()
    ):
        raise ValueError("Mismatched/replayed provider response")
    value = _object(
        document["result"], "request_id dataset provider_id model_revision points"
    )
    points = []
    for item in _array(value["points"], attempt.job.forecast.horizon):
        point = _object(item, "timestamp_ns value")
        points.append(ForecastPoint(point["timestamp_ns"], _number(point["value"])))
    result = ForecastResult(
        value["request_id"],
        _dataset(value["dataset"]),
        value["provider_id"],
        value["model_revision"],
        tuple(points),
    )
    result.validate_for(attempt.job.forecast, attempt.job.review.descriptor)
    if encode_response(attempt, result) != data:
        raise ValueError("Noncanonical provider response")
    return result
