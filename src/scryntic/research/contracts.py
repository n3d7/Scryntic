"""Strict additive v2 envelopes; no changes to historical v1 wire identities."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from scryntic.application.providers import ForecastResult
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.schemas import schema_projection
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.validation import digest, identifier, integer

_FIELDS = {
    "target": {"target_id", "units", "horizons_seconds", "price_basis"},
    "dataset-feature": {
        "original_manifest_sha256",
        "original_schema",
        "row_count",
        "availability_plane",
        "known_at_ns",
        "feature_recipe_sha256",
    },
    "forecast-distribution": {
        "request_id",
        "dataset_manifest_sha256",
        "provider_id",
        "model_revision",
        "value_kind",
        "points",
        "expected_return",
        "median_return",
        "quantiles",
        "direction_probability",
        "calibration_sha256",
        "unsupported_reason",
    },
    "experiment": {"experiment_id", "policy_sha256", "hypothesis_id", "max_trials"},
    "task": {
        "task_id",
        "experiment_sha256",
        "environment_sha256",
        "seed",
        "cpu_seconds",
        "memory_bytes",
    },
    "evidence": {"evidence_id", "class", "artifact_sha256", "forward_registered_at_ns"},
}

_SCHEMA_PREFIX = "scryntic.pi."


def _validate_common(b: dict[str, Any]) -> None:
    for key, value in b.items():
        if key.endswith("sha256") and (
            value is not None
            or key not in {"feature_recipe_sha256", "calibration_sha256"}
        ):
            digest(value)
        if key.endswith("_id") or key == "model_revision":
            identifier(value)


def _validate_target(b: dict[str, Any]) -> None:
    if b["units"] != "log-return" or b["price_basis"] != "last-trade-close":
        raise ValueError("Unsupported target semantics")
    horizons = b["horizons_seconds"]
    if type(horizons) is not list or not 1 <= len(horizons) <= 16:
        raise ValueError("Invalid duration horizons")
    for horizon in horizons:
        integer(horizon, 1)
        if horizon > 366 * 86400:
            raise ValueError("Horizon outside contract limit")
    if horizons != sorted(set(horizons)):
        raise ValueError("Horizons must be ordered and unique")


def _validate_dataset_feature(b: dict[str, Any]) -> None:
    integer(b["row_count"], 0)
    if b["availability_plane"] not in (
        "unknown",
        "historical-reconstruction",
        "as-observed",
    ):
        raise ValueError("Unsupported availability plane")
    original_schema = b["original_schema"]
    if type(original_schema) is not dict or set(original_schema) != {
        "name",
        "major",
        "minor",
    }:
        raise ValueError("Invalid original schema")
    identifier(original_schema["name"])
    integer(original_schema["major"], 1)
    integer(original_schema["minor"], 0)
    if b["known_at_ns"] is not None:
        integer(b["known_at_ns"], 0)
    if b["availability_plane"] == "as-observed" and b["known_at_ns"] is None:
        raise ValueError("Observed data requires known time evidence")


def _validate_forecast_distribution(b: dict[str, Any]) -> None:
    # PI01 adapts truthful v1 price levels; probabilistic semantics need
    # separate qualification.
    if (
        b["value_kind"] != "price-level"
        or b["unsupported_reason"] != "unsupported-by-v1"
    ):
        raise ValueError("Unsupported distribution semantics")
    if (
        any(
            b[key] is not None
            for key in (
                "expected_return",
                "median_return",
                "direction_probability",
                "calibration_sha256",
            )
        )
        or b["quantiles"] != []
    ):
        raise ValueError("v1 cannot assert calibrated distributions")
    points = b["points"]
    if type(points) is not list or len(points) > 1024:
        raise ValueError("Unbounded forecast points")
    from decimal import Decimal

    previous = -1
    for point in points:
        if type(point) is not dict or set(point) != {"timestamp_ns", "value"}:
            raise ValueError("Invalid forecast point")
        integer(point["timestamp_ns"], 0)
        if (
            point["timestamp_ns"] <= previous
            or type(point["value"]) is not str
            or not Decimal(point["value"]).is_finite()
        ):
            raise ValueError("Invalid ordered finite forecast point")
        previous = point["timestamp_ns"]


def _validate_experiment(b: dict[str, Any]) -> None:
    integer(b["max_trials"], 1)
    if b["max_trials"] > 8:
        raise ValueError("Pilot trial budget exceeded")


def _validate_task(b: dict[str, Any]) -> None:
    for key in ("seed", "cpu_seconds", "memory_bytes"):
        integer(b[key], 0 if key == "seed" else 1)
    if b["cpu_seconds"] > 90 or b["memory_bytes"] > 4294967296:
        raise ValueError("Pilot resource budget exceeded")


def _validate_evidence(b: dict[str, Any]) -> None:
    if b["class"] not in ("synthetic", "historical-reconstruction", "forward"):
        raise ValueError("Unknown evidence class")
    if b["forward_registered_at_ns"] is not None:
        integer(b["forward_registered_at_ns"], 0)
    if (b["class"] == "forward") != (b["forward_registered_at_ns"] is not None):
        raise ValueError("Forward evidence requires registration")


_VALIDATORS = {
    "target": _validate_target,
    "dataset-feature": _validate_dataset_feature,
    "forecast-distribution": _validate_forecast_distribution,
    "experiment": _validate_experiment,
    "task": _validate_task,
    "evidence": _validate_evidence,
}


def _validate(kind: str, b: dict[str, Any]) -> None:
    if kind not in _FIELDS or type(b) is not dict or set(b) != _FIELDS[kind]:
        raise ValueError("Unsupported research contract fields")
    _validate_common(b)
    _VALIDATORS[kind](b)


@dataclass(frozen=True, slots=True, init=False)
class Contract:
    kind: str
    _bytes: bytes

    def __init__(self, kind: str, body: dict[str, Any]):
        _validate(kind, body)
        raw = canonical_json_bytes(
            {
                "schema": {"name": _SCHEMA_PREFIX + kind, "major": 2, "minor": 0},
                "body": body,
            }
        )
        if len(raw) > 65536:
            raise ValueError("Research contract byte limit")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "_bytes", raw)

    @property
    def body(self) -> dict[str, Any]:
        return dict(json.loads(self._bytes)["body"])

    @property
    def canonical_bytes(self) -> bytes:
        return self._bytes

    @property
    def sha256(self) -> str:
        return sha256(self._bytes).hexdigest()


def decode_contract(raw: bytes) -> Contract:
    if type(raw) is not bytes or len(raw) > 65536:
        raise ValueError("Invalid bounded research contract")
    d = json.loads(raw)
    if type(d) is not dict or set(d) != {"schema", "body"}:
        raise ValueError("Invalid research envelope")
    s = d["schema"]
    if (
        type(s) is not dict
        or set(s) != {"name", "major", "minor"}
        or type(s["major"]) is not int
        or type(s["minor"]) is not int
        or s["major"] != 2
        or s["minor"] != 0
        or type(s["name"]) is not str
        or not s["name"].startswith(_SCHEMA_PREFIX)
    ):
        raise ValueError("Unsupported research schema")
    contract = Contract(s["name"][len(_SCHEMA_PREFIX) :], d["body"])
    if raw != contract.canonical_bytes:
        raise ValueError("Research contract is not canonical")
    return contract


def adapt_dataset(reference: DatasetRef) -> Contract:
    return Contract(
        "dataset-feature",
        {
            "original_manifest_sha256": reference.manifest_sha256,
            "original_schema": schema_projection(reference.schema),
            "row_count": reference.row_count,
            "availability_plane": "unknown",
            "known_at_ns": None,
            "feature_recipe_sha256": None,
        },
    )


def adapt_forecast(result: ForecastResult) -> Contract:
    return Contract(
        "forecast-distribution",
        {
            "request_id": result.request_id,
            "dataset_manifest_sha256": result.dataset.manifest_sha256,
            "provider_id": result.provider_id,
            "model_revision": result.model_revision,
            "value_kind": "price-level",
            "points": [
                {"timestamp_ns": p.timestamp_ns, "value": repr(p.value)}
                for p in result.points
            ],
            "expected_return": None,
            "median_return": None,
            "quantiles": [],
            "direction_probability": None,
            "calibration_sha256": None,
            "unsupported_reason": "unsupported-by-v1",
        },
    )
