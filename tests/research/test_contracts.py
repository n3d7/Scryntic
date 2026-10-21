import json

import pytest


def test_v1_adapters_preserve_identity_and_do_not_invent_probabilities() -> None:
    from scryntic.application.providers import ForecastPoint, ForecastResult
    from scryntic.research.contracts import (
        adapt_dataset,
        adapt_forecast,
        decode_contract,
    )
    from tests.jobs.helpers import job

    j = job()
    d = adapt_dataset(j.forecast.dataset)
    assert d.body["original_manifest_sha256"] == j.forecast.dataset.manifest_sha256
    assert d.body["known_at_ns"] is None
    result = ForecastResult(
        j.forecast.request_id,
        j.forecast.dataset,
        j.review.descriptor.provider_id,
        j.review.descriptor.model.revision,
        (
            ForecastPoint(
                j.inputs.verified.last_start_ns + j.forecast.frequency_ns, 2.0
            ),
        ),
    )
    f = adapt_forecast(result)
    assert f.body["direction_probability"] is None
    assert f.body["calibration_sha256"] is None
    assert f.body["value_kind"] == "price-level"
    assert decode_contract(f.canonical_bytes).canonical_bytes == f.canonical_bytes
    bad = json.loads(f.canonical_bytes)
    bad["schema"]["major"] = 99
    encoded = json.dumps(bad).encode()
    with pytest.raises(ValueError):
        decode_contract(encoded)


def test_explicit_contract_kinds_are_strict_and_horizons_are_durations() -> None:
    from scryntic.research.contracts import Contract, decode_contract

    c = Contract(
        "target",
        {
            "target_id": "returns-v1",
            "units": "log-return",
            "horizons_seconds": [3600, 86400, 604800, 2419200],
            "price_basis": "last-trade-close",
        },
    )
    assert decode_contract(c.canonical_bytes).body == c.body
    for body in ({**c.body, "horizons_seconds": [True]}, {**c.body, "extra": 1}):
        with pytest.raises((ValueError, TypeError)):
            Contract("target", body)
    for kind, body in (
        (
            "experiment",
            {
                "experiment_id": "experiment-a",
                "policy_sha256": "a" * 64,
                "hypothesis_id": "family-a",
                "max_trials": 8,
            },
        ),
        (
            "task",
            {
                "task_id": "task-a",
                "experiment_sha256": "b" * 64,
                "environment_sha256": "c" * 64,
                "seed": 42,
                "cpu_seconds": 90,
                "memory_bytes": 4294967296,
            },
        ),
        (
            "evidence",
            {
                "evidence_id": "evidence-a",
                "class": "historical-reconstruction",
                "artifact_sha256": "d" * 64,
                "forward_registered_at_ns": None,
            },
        ),
    ):
        v = Contract(kind, body)
        assert decode_contract(v.canonical_bytes).sha256 == v.sha256


def test_required_provenance_cannot_be_null() -> None:
    from scryntic.research.contracts import Contract

    for kind, body in (
        (
            "experiment",
            {
                "experiment_id": "exp-a",
                "policy_sha256": None,
                "hypothesis_id": "family-a",
                "max_trials": 8,
            },
        ),
        (
            "task",
            {
                "task_id": "task-a",
                "experiment_sha256": None,
                "environment_sha256": "a" * 64,
                "seed": 42,
                "cpu_seconds": 90,
                "memory_bytes": 4294967296,
            },
        ),
        (
            "evidence",
            {
                "evidence_id": "ev-a",
                "class": "synthetic",
                "artifact_sha256": None,
                "forward_registered_at_ns": None,
            },
        ),
    ):
        with pytest.raises((ValueError, TypeError)):
            Contract(kind, body)
