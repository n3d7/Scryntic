"""Chronological PI02 feasibility counts, independent of any model fitting."""

from __future__ import annotations

import json
import resource
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.validation import digest, integer
from scryntic.research.ledger import ResearchLedger
from scryntic.research.targets import (
    HORIZONS_SECONDS,
    HOUR_NS,
    ISSUE_DELAY_NS,
    HourlySeries,
)
from scryntic.research.vision import load_monthly_hourly


def _utc_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise ValueError("Expected UTC policy boundary")
    return int(parsed.timestamp() * 1_000_000_000)


def load_registered_spec(policy_path: Path, spec_path: Path) -> dict[str, Any]:
    """Verify the immutable PI02 decision set against registered PI00 policy."""
    spec_raw = spec_path.read_bytes()
    sidecar = spec_path.with_suffix(".sha256")
    expected = f"{sha256(spec_raw).hexdigest()}  {spec_path.name}"
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").strip() != expected:
        raise ValueError("PI02 specification digest mismatch")
    policy_raw = policy_path.read_bytes()
    policy = json.loads(policy_raw)
    spec = json.loads(spec_raw)
    if type(spec) is not dict or set(spec) != {
        "schema",
        "policy_sha256",
        "source_id",
        "availability_plane",
        "instruments",
        "source_frequency_seconds",
        "archive_months",
        "expected_rows_per_instrument",
        "expected_snapshot_sha256",
        "origin_cadence_seconds",
        "feature_lookback_hours",
        "minimum_complete_fraction",
        "horizons_seconds",
        "splits_utc",
        "embargo_seconds",
        "endpoint_rule",
        "source_use",
        "max_rows_per_instrument",
        "max_memory_bytes",
        "max_wall_seconds",
        "final_access_allowed",
        "fitting_allowed",
    }:
        raise ValueError("Unsupported PI02 specification")
    source = policy["scope"]["admitted_historical_sources"]
    verified = policy["coverage"]["verified_historical_range"]
    final_month = (
        datetime.fromisoformat(verified[1].replace("Z", "+00:00")) - timedelta(hours=1)
    ).strftime("%Y-%m")
    frozen_splits = {
        name: policy["chronology"][name]
        for name in ("development", "calibration", "exploratory_test")
    }
    if not (
        spec["schema"] == "scryntic.pi02.feasibility-spec.v1"
        and spec["policy_sha256"] == sha256(policy_raw).hexdigest()
        and policy["architecture_approved"] is True
        and policy["current_gates"]["pi00_complete"] is True
        and spec["source_id"] == "binance-vision-usdm-1h"
        and spec["availability_plane"] == "historical-reconstruction"
        and spec["instruments"] == ["BTCUSDT", "ETHUSDT"]
        and len(source) == 1
        and source[0]["venue"] == "Binance"
        and source[0]["category"] == "USD-M futures"
        and source[0]["symbols"] == spec["instruments"]
        and source[0]["use"] == "private noncommercial historical reconstruction only"
        and spec["source_frequency_seconds"]
        == policy["scope"]["candle_seconds"]
        == 3600
        and spec["archive_months"] == [verified[0][:7], final_month]
        and spec["expected_rows_per_instrument"] == 59160
        and set(spec["expected_snapshot_sha256"]) == set(spec["instruments"])
        and all(
            type(value) is str and len(value) == 64
            for value in spec["expected_snapshot_sha256"].values()
        )
        and all(
            policy["coverage"]["verified_rows"][f"Binance:USD-M:{symbol}:1h"]
            == spec["expected_rows_per_instrument"]
            for symbol in spec["instruments"]
        )
        and spec["horizons_seconds"] == policy["scope"]["horizons_seconds"]
        and spec["horizons_seconds"] == list(HORIZONS_SECONDS)
        and spec["origin_cadence_seconds"]
        == policy["origin_target"]["origin_cadence_seconds"]
        and spec["feature_lookback_hours"] == 24
        and spec["minimum_complete_fraction"] == "0.99"
        and policy["coverage"]["minimum_complete_fraction"].startswith("0.99 ")
        and spec["splits_utc"] == frozen_splits
        and spec["embargo_seconds"] == policy["chronology"]["embargo_seconds"]
        and spec["endpoint_rule"] == "strictly-before-own-split-end"
        and spec["source_use"] == "private-noncommercial-historical-reconstruction-only"
        and spec["expected_rows_per_instrument"]
        <= spec["max_rows_per_instrument"]
        <= 120000
        and 0 < spec["max_memory_bytes"] <= policy["resources"]["memory_bytes"]
        and 0 < spec["max_wall_seconds"] <= policy["resources"]["wall_seconds_per_job"]
        and spec["final_access_allowed"] is False
        and spec["fitting_allowed"] is False
    ):
        raise ValueError("PI02 specification conflicts with registered policy")
    for period in frozen_splits.values():
        _utc_ns(period[0])
        _utc_ns(period[1])
    return spec


def registered_source_use_sha256(policy: dict[str, Any]) -> str:
    """Bind admission to the admitted source grant in the frozen public policy."""
    sources = policy["scope"]["admitted_historical_sources"]
    if type(sources) is not list or len(sources) != 1:
        raise ValueError("PI02 requires one registered historical source")
    return sha256(canonical_json_bytes(sources[0])).hexdigest()


@dataclass(frozen=True, slots=True)
class Split:
    name: str
    start_ns: int
    end_ns: int
    embargo_ns: int

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("Unnamed feasibility split")
        integer(self.start_ns, 0)
        integer(self.end_ns, 1)
        integer(self.embargo_ns, 0)
        if (
            self.start_ns >= self.end_ns
            or self.embargo_ns >= self.end_ns - self.start_ns
        ):
            raise ValueError("Invalid chronological split")


@dataclass(frozen=True, slots=True)
class HorizonCounts:
    candidates: int
    structurally_eligible: int
    origin_eligible: int
    matured: int
    nonoverlapping_blocks: int
    completeness: float
    structural_exclusions: dict[str, int]
    data_exclusions: dict[str, int]


def _structural_exclusion(
    series: HourlySeries,
    split: Split,
    origin_ns: int,
    horizon_ns: int,
    lookback_hours: int,
) -> str | None:
    if origin_ns < split.start_ns + split.embargo_ns:
        return "embargo"
    if origin_ns - lookback_hours * HOUR_NS < series.first_start_ns:
        return "missing-history-boundary"
    if origin_ns + horizon_ns >= split.end_ns:
        return "split-endpoint"
    return None


def _require_horizon_grid(
    series: HourlySeries,
    split: Split,
    horizon_seconds: int,
    cadence_seconds: int,
    lookback_hours: int,
    minimum_block_seconds: int,
    policy_sha256: str,
) -> None:
    if type(series) is not HourlySeries or type(split) is not Split:
        raise TypeError("Expected hourly series and chronological split")
    digest(policy_sha256)
    integer(cadence_seconds, 3600)
    integer(minimum_block_seconds, horizon_seconds)
    if (
        horizon_seconds not in HORIZONS_SECONDS
        or cadence_seconds not in (3600, 86400)
        or minimum_block_seconds % 3600
        or lookback_hours < 1
        or lookback_hours > 672
    ):
        raise ValueError("Unsupported bounded PI02 feasibility grid")


def assess_horizon(
    series: HourlySeries,
    split: Split,
    horizon_seconds: int,
    cadence_seconds: int,
    lookback_hours: int,
    minimum_block_seconds: int,
    policy_sha256: str,
) -> HorizonCounts:
    """Count allowed origins, matured labels and conservative time blocks."""
    _require_horizon_grid(
        series,
        split,
        horizon_seconds,
        cadence_seconds,
        lookback_hours,
        minimum_block_seconds,
        policy_sha256,
    )
    cadence_ns = cadence_seconds * 1_000_000_000
    horizon_ns = horizon_seconds * 1_000_000_000
    block_ns = minimum_block_seconds * 1_000_000_000
    first = ((split.start_ns + cadence_ns - 1) // cadence_ns) * cadence_ns
    candidates = 0
    structural: Counter[str] = Counter()
    data: Counter[str] = Counter()
    structural_count = 0
    origin_count = 0
    matured = 0
    blocks = 0
    next_block_ns = 0
    for origin_ns in range(first, split.end_ns, cadence_ns):
        candidates += 1
        reason = _structural_exclusion(
            series, split, origin_ns, horizon_ns, lookback_hours
        )
        if reason is not None:
            structural[reason] += 1
            continue
        structural_count += 1
        origin = series.origin(
            origin_ns, horizon_seconds, policy_sha256, lookback_hours=lookback_hours
        )
        if not origin.eligible:
            data[origin.reason or "origin-unavailable"] += 1
            continue
        origin_count += 1
        outcome = series.outcome(
            origin,
            origin_ns + horizon_ns + ISSUE_DELAY_NS
            if series.key.availability_plane == "as-observed"
            else None,
        )
        if outcome.value is None:
            data[outcome.reason or "label-unavailable"] += 1
            continue
        matured += 1
        if origin_ns >= next_block_ns:
            blocks += 1
            next_block_ns = origin_ns + block_ns
    return HorizonCounts(
        candidates=candidates,
        structurally_eligible=structural_count,
        origin_eligible=origin_count,
        matured=matured,
        nonoverlapping_blocks=blocks,
        completeness=matured / structural_count if structural_count else 0.0,
        structural_exclusions=dict(sorted(structural.items())),
        data_exclusions=dict(sorted(data.items())),
    )


def development_input_manifest(
    series: HourlySeries, policy_sha256: str, source_use_sha256: str
) -> dict[str, Any]:
    """Describe exactly one label-free, verified development snapshot."""
    if type(series) is not HourlySeries:
        raise TypeError("Expected hourly series")
    digest(policy_sha256)
    digest(source_use_sha256)
    return {
        "schema": "scryntic.pi02.development-input.v1",
        "policy_sha256": policy_sha256,
        "source_id": series.key.source_id,
        "snapshot_sha256": series.key.snapshot_sha256,
        "source_use_sha256": source_use_sha256,
        "instrument": series.key.instrument,
        "availability_plane": series.key.availability_plane,
        "time_range_ns": [series.first_start_ns, series.last_end_ns],
        "row_count": series.row_count,
        "label_horizon_seconds": 0,
    }


def _months(first: str, last: str) -> tuple[str, ...]:
    year, month = (int(part) for part in first.split("-"))
    result: list[str] = []
    while f"{year:04d}-{month:02d}" <= last:
        result.append(f"{year:04d}-{month:02d}")
        if len(result) > 81:
            raise ValueError("Unbounded monthly archive range")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return tuple(result)


def _split_counts(
    series: HourlySeries,
    spec: dict[str, Any],
    policy: dict[str, Any],
    policy_sha256: str,
    started: float,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for split_name, boundaries in spec["splits_utc"].items():
        split = Split(
            split_name,
            _utc_ns(boundaries[0]),
            _utc_ns(boundaries[1]),
            spec["embargo_seconds"] * 1_000_000_000
            if split_name != "development"
            else 0,
        )
        horizon_counts: dict[str, Any] = {}
        for horizon in spec["horizons_seconds"]:
            cadence = spec["origin_cadence_seconds"][str(horizon)]
            minimum_block = policy["evaluation"]["minimum_block_duration_seconds"][
                str(horizon)
            ]
            measured = assess_horizon(
                series,
                split,
                horizon,
                cadence,
                spec["feature_lookback_hours"],
                minimum_block,
                policy_sha256,
            )
            result = asdict(measured)
            result["completeness"] = (
                f"{measured.matured}/{measured.structurally_eligible}"
            )
            horizon_counts[str(horizon)] = result
            if time.perf_counter() - started > spec["max_wall_seconds"]:
                raise TimeoutError("PI02 feasibility exceeded registered wall budget")
        counts[split_name] = horizon_counts
    return counts


def run_registered_feasibility(
    policy_path: Path,
    spec_path: Path,
    archive_root: Path,
    ledger: ResearchLedger,
) -> dict[str, Any]:
    """Verify existing offline archives, audit admission, then count development labels.

    This function neither fits a model nor opens the registered future final range.
    The caller supplies an already active PI01 ledger for this isolated run.
    """
    started = time.perf_counter()
    spec = load_registered_spec(policy_path, spec_path)
    if type(ledger) is not ResearchLedger:
        raise TypeError("PI02 requires an active protected research ledger")
    policy = json.loads(policy_path.read_bytes())
    rights_sha256 = registered_source_use_sha256(policy)
    policy_sha256 = spec["policy_sha256"]
    months = _months(*spec["archive_months"])
    if len(months) != 81:
        raise ValueError("Registered source history is incomplete")
    coverage_start, coverage_end = (
        _utc_ns(value) for value in policy["coverage"]["verified_historical_range"]
    )
    counts: dict[str, dict[str, dict[str, Any]]] = {}
    inputs: dict[str, dict[str, Any]] = {}
    for instrument in spec["instruments"]:
        series = load_monthly_hourly(archive_root, instrument, months)
        if (
            series.row_count != spec["expected_rows_per_instrument"]
            or series.row_count > spec["max_rows_per_instrument"]
            or series.key.snapshot_sha256
            != spec["expected_snapshot_sha256"][instrument]
            or (series.first_start_ns, series.last_end_ns)
            != (coverage_start, coverage_end)
        ):
            raise ValueError("Archive history differs from the registered coverage")
        input_manifest = development_input_manifest(
            series, policy_sha256, rights_sha256
        )
        input_sha256 = sha256(canonical_json_bytes(input_manifest)).hexdigest()
        ledger.check_pi02_input(input_sha256, input_manifest)
        inputs[instrument] = {
            "manifest_sha256": input_sha256,
            "snapshot_sha256": series.key.snapshot_sha256,
            "row_count": series.row_count,
            "time_range_ns": input_manifest["time_range_ns"],
        }
        counts[instrument] = _split_counts(series, spec, policy, policy_sha256, started)
    gates: dict[str, Any] = {}
    for horizon in spec["horizons_seconds"]:
        key = str(horizon)
        baseline_ready = all(
            counts[instrument][split_name][key]["matured"] > 0
            and counts[instrument][split_name][key]["matured"] * 100
            >= counts[instrument][split_name][key]["structurally_eligible"] * 99
            for instrument in spec["instruments"]
            for split_name in spec["splits_utc"]
        )
        observed_blocks = min(
            counts[instrument]["exploratory_test"][key]["nonoverlapping_blocks"]
            for instrument in spec["instruments"]
        )
        claim_floor = policy["evaluation"]["minimum_nonoverlapping_blocks"][key]
        gates[key] = {
            "minimum_complete_history_hours": spec["feature_lookback_hours"]
            + horizon // 3600,
            "duration_expressible": horizon % spec["source_frequency_seconds"] == 0,
            "baseline_development_ready": baseline_ready,
            "exploratory_nonoverlapping_blocks_min_across_instruments": observed_blocks,
            "registered_claim_block_floor": claim_floor,
            "exploratory_claim_floor_met": observed_blocks >= claim_floor,
        }
    wall_seconds = time.perf_counter() - started
    peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if (
        wall_seconds > spec["max_wall_seconds"]
        or peak_rss_bytes > spec["max_memory_bytes"]
    ):
        raise ValueError("PI02 feasibility exceeded registered resource bound")
    return {
        "schema": "scryntic.pi02.feasibility-result.v1",
        "policy_sha256": policy_sha256,
        "spec_sha256": sha256(spec_path.read_bytes()).hexdigest(),
        "source_use_sha256": rights_sha256,
        "evidence_class": "historical-reconstruction",
        "inputs": inputs,
        "counts": counts,
        "gates": gates,
        "resource_measurement": {
            "wall_seconds": f"{wall_seconds:.3f}",
            "process_peak_rss_bytes": peak_rss_bytes,
        },
        "final_labels_accessed": False,
        "model_fitted": False,
    }
