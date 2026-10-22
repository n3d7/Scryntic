"""PI02 counts reflect chronological eligibility rather than raw row totals."""

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.research.feasibility import (
    Split,
    assess_horizon,
    development_input_manifest,
    load_registered_spec,
    registered_source_use_sha256,
)
from scryntic.research.targets import HOUR_NS, HourlyBar, HourlySeries, SeriesKey


def _series(hours: int, *, gap: int | None = None) -> HourlySeries:
    return HourlySeries(
        SeriesKey(
            "binance-vision-usdm-1h",
            "Binance",
            "USD-M",
            "BTCUSDT",
            "a" * 64,
            "historical-reconstruction",
        ),
        [
            HourlyBar(hour * HOUR_NS, Decimal("100"), f"{hour + 1:064x}")
            for hour in range(hours)
            if hour != gap
        ],
    )


def test_feasibility_separates_boundary_purge_from_data_gaps() -> None:
    split = Split("development", 0, 100 * HOUR_NS, 0)
    complete = assess_horizon(_series(100), split, 3600, 3600, 24, 86400, "c" * 64)
    assert complete.candidates == 100
    assert complete.structurally_eligible == 75
    assert complete.matured == 75
    assert complete.nonoverlapping_blocks == 4
    assert complete.completeness == 1
    assert complete.structural_exclusions == {
        "missing-history-boundary": 24,
        "split-endpoint": 1,
    }

    missing = assess_horizon(
        _series(100, gap=50), split, 3600, 3600, 24, 86400, "c" * 64
    )
    assert missing.structurally_eligible == 75
    assert missing.matured == 50
    assert missing.data_exclusions == {"missing-constituent": 25}
    assert missing.completeness < 0.99


def test_embargo_and_daily_cadence_are_explicit() -> None:
    split = Split("validation", 0, 240 * HOUR_NS, 2 * 24 * HOUR_NS)
    counts = assess_horizon(_series(240), split, 86400, 86400, 24, 86400, "c" * 64)
    assert counts.candidates == 10
    assert counts.structurally_eligible == 7
    assert counts.matured == 7
    assert counts.structural_exclusions == {"embargo": 2, "split-endpoint": 1}


def test_registered_spec_rejects_scientific_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    policy = root / "docs/architecture/PI00_RESEARCH_POLICY_v2.json"
    spec = root / "docs/architecture/PI02_FEASIBILITY_SPEC_v1.json"
    registered = load_registered_spec(policy, spec)
    assert registered["horizons_seconds"] == [3600, 86400, 604800, 2419200]
    altered = json.loads(spec.read_text())
    altered["final_access_allowed"] = True
    modified = tmp_path / spec.name
    modified.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="digest"):
        load_registered_spec(policy, modified)


def test_development_manifest_contains_exact_verified_bounds_and_no_labels() -> None:
    series = _series(100)
    manifest = development_input_manifest(series, "c" * 64, "d" * 64)
    assert manifest["time_range_ns"] == [0, 100 * HOUR_NS]
    assert manifest["snapshot_sha256"] == series.key.snapshot_sha256
    assert manifest["label_horizon_seconds"] == 0
    assert manifest["availability_plane"] == "historical-reconstruction"


def test_source_use_digest_is_bound_to_registered_public_policy_grant() -> None:
    root = Path(__file__).resolve().parents[2]
    policy = json.loads(
        (root / "docs/architecture/PI00_RESEARCH_POLICY_v2.json").read_text()
    )
    grant = policy["scope"]["admitted_historical_sources"][0]
    assert (
        registered_source_use_sha256(policy)
        == sha256(canonical_json_bytes(grant)).hexdigest()
    )
