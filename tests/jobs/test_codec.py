"""Wire identities, versions and byte/shape bounds reject hostile responses."""

import json

import pytest

from scryntic.application.providers import ForecastPoint, ForecastResult
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.contracts import JobAttempt
from tests.jobs.helpers import LAST, STEP, job


def response() -> tuple[JobAttempt, ForecastResult, bytes]:
    from scryntic.jobs.codec import encode_response
    from scryntic.jobs.contracts import JobAttempt

    value = job()
    attempt = JobAttempt(value, 1, "a" * 32)
    result = ForecastResult(
        value.job_id,
        value.forecast.dataset,
        value.review.descriptor.provider_id,
        value.review.descriptor.model.revision,
        (ForecastPoint(LAST + STEP, 2.0), ForecastPoint(LAST + 2 * STEP, 2.0)),
    )
    return attempt, result, encode_response(attempt, result)


def test_job_attempt_and_response_round_trip_preserves_owned_contracts() -> None:
    from scryntic.jobs.codec import (
        decode_attempt,
        decode_job,
        decode_response,
        encode_attempt,
        encode_job,
    )

    attempt, result, data = response()
    assert decode_job(encode_job(attempt.job)) == attempt.job
    assert decode_attempt(encode_attempt(attempt)) == attempt
    assert decode_response(data, attempt) == result


@pytest.mark.parametrize(
    "defect",
    [
        "version",
        "job",
        "attempt",
        "token",
        "request",
        "extra",
        "provider",
        "revision",
        "dataset",
        "rows",
        "horizon",
        "nonfinite",
        "boolean_time",
    ],
)
def test_malformed_or_mismatched_response_is_never_a_result(defect: str) -> None:
    from scryntic.jobs.codec import decode_response

    attempt, _, data = response()
    value = json.loads(data)
    if defect == "version":
        value["schema"]["version"]["major"] = 2
    elif defect == "job":
        value["job_sha256"] = "b" * 64
    elif defect == "attempt":
        value["attempt"] = 2
    elif defect == "token":
        value["token"] = "b" * 32
    elif defect == "request":
        value["request_sha256"] = "b" * 64
    elif defect == "extra":
        value["loader"] = "downloaded-code"
    elif defect == "provider":
        value["result"]["provider_id"] = "other"
    elif defect == "revision":
        value["result"]["model_revision"] = "other"
    elif defect == "dataset":
        value["result"]["dataset"]["manifest_sha256"] = "b" * 64
    elif defect == "rows":
        value["result"]["dataset"]["row_count"] = 3
    elif defect == "horizon":
        value["result"]["points"] *= 2
    elif defect == "nonfinite":
        value["result"]["points"][0]["value"] = "nan"
    else:
        value["result"]["points"][0]["timestamp_ns"] = True
    malformed = canonical_json_bytes(value)
    with pytest.raises((ValueError, TypeError)):
        decode_response(malformed, attempt)


@pytest.mark.parametrize(
    "data",
    [
        b"x" * 65_537,
        b"{}",
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b"[1]",
        b"\xff",
        b"[" * 1000,
    ],
)
def test_invalid_json_is_bounded_before_acceptance(data: bytes) -> None:
    from scryntic.jobs.codec import decode_response

    attempt, _, _ = response()
    with pytest.raises(ValueError):
        decode_response(data, attempt)
