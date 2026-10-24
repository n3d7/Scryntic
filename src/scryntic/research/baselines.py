"""Frozen PI03 admission and development-only chronology, without model fitting."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import isfinite, log, sqrt
from pathlib import Path
from statistics import fmean
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.validation import digest

SPEC_SHA256 = "2ba095a3763b16a28b6c58b30b2f37f42bdeca1cbf669beb43a79ce5963cd099"
SPEC_CANONICAL_SHA256 = (
    "26117e6a1228282d095a90112f78b86b5fe4d0ffc93cbd76fc50e12b8344e626"
)
SOURCE_USE_SHA256 = "526b691d6841764a599d2264dabade6398a49ae8e8cc38bfae5cb5d13afd6848"
REQUEST_BYTES = 4 * 1024**2
RESPONSE_BYTES = 2 * 1024**2
HOUR = 3600 * 10**9
DAY = 24 * HOUR
DELAY = 60 * 10**9
ALGORITHMS = (
    "zero",
    "historical-mean",
    "ridge-ar-1",
    "ridge-ar-24",
    "volatility-empirical",
)


def utc_ns(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()) * 10**9


def body_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def check_spec(spec: dict[str, Any]) -> None:
    if body_sha256(spec) != SPEC_CANONICAL_SHA256:
        raise ValueError("PI03 specification differs from the frozen admission")


def load_spec(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SPEC_SHA256:
        raise ValueError("PI03 specification byte identity mismatch")
    value = json.loads(raw)
    check_spec(value)
    return value  # type: ignore[no-any-return]


def structural_counts(spec: dict[str, Any]) -> dict[str, tuple[int, int]]:
    check_spec(spec)
    start, end = map(utc_ns, spec["folds"][0]["evaluation_origins_utc"])
    result = {}
    for horizon in spec["horizons_seconds"]:
        cadence = spec["origin_cadence_seconds"][str(horizon)] * 10**9
        duration = max(86400, horizon) * 10**9
        count = (end - start - horizon * 10**9 - DELAY - 1) // cadence + 1
        result[str(horizon)] = (count, (count * cadence + duration - 1) // duration)
    return result


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate PI03 field")
        result[key] = value
    return result


def validate_input(data: bytes) -> dict[str, Any]:
    """Validate fixed source pins and label-free development prices before access."""
    if type(data) is not bytes or not 0 < len(data) <= REQUEST_BYTES:
        raise ValueError("PI03 input byte bound")
    value = json.loads(data, object_pairs_hook=_pairs)
    fields = {"schema", "spec", "source_use_sha256", "series"}
    if (
        type(value) is not dict
        or set(value) != fields
        or canonical_json_bytes(value) != data
    ):
        raise ValueError("Noncanonical PI03 input")
    spec = value["spec"]
    check_spec(spec)
    digest(value["source_use_sha256"])
    if value["source_use_sha256"] != SOURCE_USE_SHA256:
        raise ValueError("Unregistered PI03 source rights")
    if value["schema"] != "scryntic.pi03.development-input.v1" or set(
        value["series"]
    ) != set(spec["instruments"]):
        raise ValueError("Unregistered PI03 input schema/universe")
    start, end = map(utc_ns, spec["development_utc"])
    for asset, series in value["series"].items():
        if type(series) is not dict or set(series) != {
            "start_ns",
            "closes",
            "snapshot_sha256",
        }:
            raise ValueError("Invalid bounded PI03 series")
        closes = series["closes"]
        if (
            type(series["start_ns"]) is not int
            or series["start_ns"] != start
            or series["snapshot_sha256"] != spec["snapshot_sha256"][asset]
            or type(closes) is not list
            or len(closes) != (end - start) // HOUR
        ):
            raise ValueError("PI03 input crosses or differs from development")
        for close in closes:
            if close is not None and (
                type(close) is not str
                or len(close) > 64
                or not isfinite(float(close))
                or float(close) <= 0
            ):
                raise ValueError("Invalid PI03 price")
    return value


@dataclass(frozen=True, slots=True)
class Row:
    asset: str
    origin_ns: int
    horizon: int
    features: tuple[float, ...]
    target: float


def eligible_indices(
    value: dict[str, Any], horizon: int, training: bool
) -> tuple[list[int], dict[str, int]]:
    """Count common origin eligibility using bounds and missingness, never returns."""
    spec = value["spec"]
    fold = spec["folds"][0]
    key = "training_origins_utc" if training else "evaluation_origins_utc"
    start, end = map(utc_ns, fold[key])
    cutoff = utc_ns(fold["fit_cutoff_utc"]) if training else end
    base = utc_ns(spec["development_utc"][0])
    cadence = spec["origin_cadence_seconds"][str(horizon)] * 10**9
    steps = horizon // 3600
    # Prefix sums let an actual hourly gap invalidate the entire label interval.
    missing = []
    for series in value["series"].values():
        prefix = [0]
        for close in series["closes"]:
            prefix.append(prefix[-1] + (close is None))
        missing.append(prefix)
    selected: list[int] = []
    excluded: Counter[str] = Counter()
    for origin in range(start, end, cadence):
        i = (origin - base) // HOUR - 1
        if origin + horizon * 10**9 + DELAY >= cutoff:
            excluded["immature-or-boundary-label"] += 1
        elif i < 24:
            excluded["missing-feature-history"] += 1
        elif (
            not training
            and origin - 24 * HOUR
            <= utc_ns(fold["fit_cutoff_utc"]) + spec["embargo_seconds"] * 10**9
        ):
            excluded["purge-or-embargo"] += 1
        elif any(p[i + steps + 1] != p[i - 24] for p in missing):
            excluded["incomplete-common-pair"] += 1
        else:
            selected.append(i)
    return selected, dict(sorted(excluded.items()))


def prefit_counts(data: bytes) -> dict[str, Any]:
    value = validate_input(data)
    spec = value["spec"]
    base = utc_ns(spec["development_utc"][0])
    dependence = training_dependence(value)
    report: dict[str, Any] = {}
    for horizon in spec["horizons_seconds"]:
        indices, exclusions = eligible_indices(value, horizon, False)
        blocks = 0
        next_origin = 0
        adjusted_blocks = 0
        next_adjusted = 0
        duration = max(86400, horizon) * 10**9
        adjusted_duration = max(duration, dependence * DAY)
        for i in indices:
            origin = base + (i + 1) * HOUR
            if origin >= next_origin:
                blocks += 1
                next_origin = origin + duration
            if origin >= next_adjusted:
                adjusted_blocks += 1
                next_adjusted = origin + adjusted_duration
        report[str(horizon)] = {
            "paired_origins_per_asset": len(indices),
            "nonoverlapping_minimum_duration_blocks": blocks,
            "dependence_adjusted_nonoverlapping_blocks": adjusted_blocks,
            "block_duration_seconds": adjusted_duration // 10**9,
            "claim_floor_met": adjusted_blocks
            >= spec["comparison"]["minimum_blocks"][str(horizon)],
            "exclusions": exclusions,
        }
    return {
        "schema": "scryntic.pi03.prefit-counts.v1",
        "input_sha256": sha256(data).hexdigest(),
        "spec_sha256": SPEC_SHA256,
        "fold": "forward-development-1",
        "training_dependence_days": dependence,
        "horizons": report,
        "model_fitted": False,
        "final_labels_accessed": False,
        "independence": "structural bounds; training-estimated dependence may reduce effective counts",
    }


def rows(value: dict[str, Any], asset: str, horizon: int, training: bool) -> list[Row]:
    indices, _ = eligible_indices(value, horizon, training)
    series = value["series"][asset]
    prices = [float(x) if x is not None else 0.0 for x in series["closes"]]
    steps = horizon // 3600
    return [
        Row(
            asset,
            series["start_ns"] + (i + 1) * HOUR,
            horizon,
            tuple(log(prices[i - lag]) - log(prices[i - lag - 1]) for lag in range(24)),
            log(prices[i + steps]) - log(prices[i]),
        )
        for i in indices
    ]


def prepare_input(
    policy_path: Path, pi02_path: Path, spec_path: Path, archive_root: Path, ledger: Any
) -> bytes:
    """Read verified retained archives; export development prices only, no fitting."""
    from scryntic.research.feasibility import (
        _months,
        development_input_manifest,
        load_registered_spec,
        registered_source_use_sha256,
    )
    from scryntic.research.ledger import ResearchLedger
    from scryntic.research.targets import HourlyBar, HourlySeries
    from scryntic.research.vision import load_monthly_hourly

    spec = load_spec(spec_path)
    old = load_registered_spec(policy_path, pi02_path)
    if (
        type(ledger) is not ResearchLedger
        or ledger.reservation.policy_sha256 != spec["policy_sha256"]
    ):
        raise ValueError("PI03 requires the active frozen PI01 reservation")
    if sha256(pi02_path.read_bytes()).hexdigest() != spec["feasibility_spec_sha256"]:
        raise ValueError("PI02 predecessor changed")
    policy = json.loads(policy_path.read_bytes())
    source_use = registered_source_use_sha256(policy)
    start, end = map(utc_ns, spec["development_utc"])
    inputs: dict[str, Any] = {}
    for asset in spec["instruments"]:
        series = load_monthly_hourly(
            archive_root, asset, _months(*old["archive_months"])
        )
        if (
            series.key.snapshot_sha256 != spec["snapshot_sha256"][asset]
            or series.row_count != 59160
        ):
            raise ValueError("PI03 source differs from the registered snapshot")
        # The trusted source reader verified the complete retained snapshot. Only
        # development bars cross the model boundary; no later outcomes are built.
        sampled = series.resample(start, end, None, interval_ns=HOUR)
        bars = [
            HourlyBar(bar.start_ns, bar.close, bar.raw_lineage_sha256)
            for bar in sampled.bars
        ]
        development = HourlySeries(series.key, bars)
        manifest = development_input_manifest(
            development, spec["policy_sha256"], source_use
        )
        ledger.check_pi02_input(body_sha256(manifest), manifest)
        closes = {bar.start_ns: str(bar.close) for bar in sampled.bars}
        inputs[asset] = {
            "start_ns": start,
            "closes": [closes.get(t) for t in range(start, end, HOUR)],
            "snapshot_sha256": series.key.snapshot_sha256,
        }
    data = canonical_json_bytes(
        {
            "schema": "scryntic.pi03.development-input.v1",
            "spec": spec,
            "source_use_sha256": source_use,
            "series": inputs,
        }
    )
    validate_input(data)
    return data


def training_dependence(value: dict[str, Any]) -> int:
    """Conservative predeclared dependence diagnostic using training prices only."""
    cutoff = utc_ns(value["spec"]["folds"][0]["fit_cutoff_utc"])
    span = 1
    for series in value["series"].values():
        count = (cutoff - series["start_ns"]) // HOUR
        closes = series["closes"][: count - 1]
        daily: dict[int, list[float]] = defaultdict(list)
        for i in range(1, len(closes)):
            if closes[i] is not None and closes[i - 1] is not None:
                daily[(series["start_ns"] + (i + 1) * HOUR) // DAY].append(
                    log(float(closes[i]) / float(closes[i - 1]))
                )
        for absolute in (False, True):
            points = [
                fmean(abs(x) if absolute else x for x in v)
                for _, v in sorted(daily.items())
                if len(v) == 24
            ]
            if len(points) < 114:
                raise ValueError(
                    "Insufficient training history for dependence diagnostic"
                )
            mean = fmean(points)
            centered = [x - mean for x in points]
            denominator = sum(x * x for x in centered)
            if denominator:
                for lag in range(1, 113):
                    correlation = (
                        sum(
                            a * b
                            for a, b in zip(
                                centered[lag:], centered[:-lag], strict=True
                            )
                        )
                        / denominator
                    )
                    if abs(correlation) > 2 / sqrt(len(points)):
                        span = max(span, lag + 1)
    return span
