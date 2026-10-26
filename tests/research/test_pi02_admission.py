"""PI02 development admission preserves PI01 default-deny behavior."""

from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from scryntic.archive.canonical import JsonObject, canonical_json_bytes
from scryntic.research.ledger import ResearchDenied, ResearchLedger, Reservation
from tests.archive.helpers import installation


def _input(
    *, end: int = 90, policy: str = "a" * 64, plane: str = "historical-reconstruction"
) -> dict[str, object]:
    return {
        "schema": "scryntic.pi02.development-input.v1",
        "policy_sha256": policy,
        "source_id": "binance-vision-usdm-1h",
        "snapshot_sha256": "b" * 64,
        "source_use_sha256": "c" * 64,
        "instrument": "BTCUSDT",
        "availability_plane": plane,
        "time_range_ns": [10, end],
        "row_count": 2,
        "label_horizon_seconds": 0,
    }


def test_protected_pi02_input_is_exactly_scoped_and_audited(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with ResearchLedger(root, clock=lambda: 1) as ledger:
        ledger.reserve(Reservation("a" * 64, 100, 200, 90))
        valid = _input()
        ledger.check_pi02_input(
            sha256(canonical_json_bytes(cast(JsonObject, valid))).hexdigest(), valid
        )
        for invalid in (
            _input(end=91),
            _input(policy="f" * 64),
            _input(plane="as-observed"),
            {**_input(), "source_id": "bybit"},
            {**_input(), "label_horizon_seconds": 3600},
            {**_input(), "unknown": True},
        ):
            invalid_sha256 = sha256(
                canonical_json_bytes(cast(JsonObject, invalid))
            ).hexdigest()
            with pytest.raises(ResearchDenied):
                ledger.check_pi02_input(invalid_sha256, invalid)
        with pytest.raises(ResearchDenied):
            ledger.check_dataset("d" * 64, {"time_range_ns": [10, 80]}, "build")
        assert sum(event["kind"] == "access_allowed" for event in ledger.events) == 1
        assert sum(event["kind"] == "access_denied" for event in ledger.events) == 7
