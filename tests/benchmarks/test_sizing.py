"""Configuration sizes actual retained copies, not just the raw archive."""

from dataclasses import replace

import pytest


def test_ninety_day_budget_includes_spool_manifests_and_headroom() -> None:
    from scryntic.benchmarks.sizing import FamilyRate, SizingConfig, size_envelope

    family = FamilyRate("candle", 20, 1 / 60, 400, 1200, 900, 800)
    config = SizingConfig(families=(family,), horizon_days=90, spool_days=90)
    result = size_envelope(config)
    assert result["records"] == 2_592_000
    assert result["archive_bytes"] == 2_592_000 * 400
    assert result["spool_bytes"] == 2_592_000 * 900
    assert result["derived_bytes"] == 2_592_000 * 800
    assert result["manifest_catalog_bytes"] > 0
    assert result["required_disk_bytes"] > result["retained_bytes"]
    doubled = size_envelope(replace(config, horizon_days=180, spool_days=180))
    assert doubled["archive_bytes"] == 2 * result["archive_bytes"]


def test_queue_accounts_for_peak_backlog_and_inflight_segment() -> None:
    from scryntic.benchmarks.sizing import FamilyRate, SizingConfig, size_envelope

    config = SizingConfig(
        families=(FamilyRate("trade", 1, 10, 300, 1000, 800, 0),),
        peak_records_per_second=1000,
        burst_seconds=3,
        measured_service_records_per_second=500,
        service_fraction=0.5,
        queue_seconds=1,
        segment_records=128,
        max_payload_bytes=4096,
    )
    result = size_envelope(config)
    assert result["service_records_per_second"] == 250
    assert result["burst_backlog_records"] == 2250
    assert result["queue_records"] == 2250
    assert result["queue_payload_bytes"] == (2250 + 128 + 1) * 4096
    assert result["queue_memory_bytes"] >= result["queue_payload_bytes"]
    assert result["sustained_utilization"] == 0.04
    overloaded = size_envelope(replace(config, measured_service_records_per_second=10))
    assert overloaded["sustained_within_budget"] is False


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True])
def test_sizing_rejects_invalid_rate(value: float) -> None:
    from scryntic.benchmarks.sizing import FamilyRate

    with pytest.raises((ValueError, TypeError)):
        FamilyRate("trade", 20, value, 400, 1000, 800, 0)


def test_json_configuration_is_closed_and_horizon_is_configurable() -> None:
    from scryntic.benchmarks.sizing import config_from_dict, size_envelope

    settings: dict[str, object] = {
        "horizon_days": 1,
        "spool_days": 1,
        "families": [
            {
                "name": "candle",
                "instruments": 5,
                "envelopes_per_instrument_second": 1 / 60,
                "encoded_record_bytes": 400,
                "logical_record_bytes": 1200,
                "spool_record_bytes": 900,
                "derived_record_bytes": 800,
            }
        ],
    }
    assert size_envelope(config_from_dict(settings))["records"] == 7200
    with pytest.raises(TypeError):
        config_from_dict(settings | {"codec_to_import": "unapproved"})


def test_unpruned_spool_cannot_be_budgeted_for_less_than_archive_horizon() -> None:
    from scryntic.benchmarks.sizing import FamilyRate, SizingConfig

    family = FamilyRate("candle", 20, 1 / 60, 400, 1200, 900, 800)
    with pytest.raises(ValueError, match="spool"):
        SizingConfig(families=(family,), horizon_days=90, spool_days=1)
