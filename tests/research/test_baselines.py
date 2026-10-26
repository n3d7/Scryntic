import copy
import unittest
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.research.baselines import (
    HOUR,
    SOURCE_USE_SHA256,
    load_spec,
    prefit_counts,
    rows,
    structural_counts,
    utc_ns,
    validate_input,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/architecture/PI03_BASELINE_SPEC_v1.json"


class AdmissionTests(unittest.TestCase):
    def input(self) -> dict[str, Any]:
        spec = load_spec(SPEC)
        start, end = map(utc_ns, spec["development_utc"])
        return {
            "schema": "scryntic.pi03.development-input.v1",
            "spec": spec,
            "source_use_sha256": SOURCE_USE_SHA256,
            "series": {
                asset: {
                    "start_ns": start,
                    "closes": ["100.0"] * ((end - start) // HOUR),
                    "snapshot_sha256": spec["snapshot_sha256"][asset],
                }
                for asset in spec["instruments"]
            },
        }

    def test_frozen_spec_and_all_interval_block_counts(self) -> None:
        spec = load_spec(SPEC)
        self.assertEqual(
            structural_counts(spec),
            {
                "3600": (29231, 1218),
                "86400": (1217, 1217),
                "604800": (1211, 173),
                "2419200": (1190, 43),
            },
        )
        changed = copy.deepcopy(spec)
        changed["final_access_allowed"] = True
        with self.assertRaises(ValueError):
            structural_counts(changed)

    def test_unbound_and_final_inputs_are_denied(self) -> None:
        with self.assertRaises(ValueError):
            validate_input(b'{"final_access_allowed":true}')

    def test_actual_metadata_counts_match_registered_upper_bounds(self) -> None:
        report = prefit_counts(canonical_json_bytes(self.input()))
        self.assertEqual(
            [
                v["nonoverlapping_minimum_duration_blocks"]
                for v in report["horizons"].values()
            ],
            [1218, 1217, 173, 43],
        )
        self.assertFalse(report["model_fitted"])

    def test_gap_excludes_common_pairs_without_filling(self) -> None:
        value = self.input()
        i = (
            utc_ns("2022-01-01T00:00:00Z") - value["series"]["BTCUSDT"]["start_ns"]
        ) // HOUR
        value["series"]["BTCUSDT"]["closes"][i] = None
        report = prefit_counts(canonical_json_bytes(value))
        self.assertGreater(
            report["horizons"]["2419200"]["exclusions"]["incomplete-common-pair"], 0
        )
        self.assertLess(report["horizons"]["3600"]["paired_origins_per_asset"], 29231)

    def test_training_labels_mature_before_cutoff_and_ignore_future_changes(
        self,
    ) -> None:
        value = self.input()
        before = rows(value, "BTCUSDT", 2419200, True)
        cutoff = utc_ns(value["spec"]["folds"][0]["fit_cutoff_utc"])
        self.assertTrue(
            all(r.origin_ns + r.horizon * 10**9 + 60 * 10**9 < cutoff for r in before)
        )
        i = (cutoff - value["series"]["BTCUSDT"]["start_ns"]) // HOUR
        value["series"]["BTCUSDT"]["closes"][i:] = ["9999.0"] * (
            len(value["series"]["BTCUSDT"]["closes"]) - i
        )
        self.assertEqual(rows(value, "BTCUSDT", 2419200, True), before)

    def test_wrong_universe_snapshot_rights_and_extra_future_bar_fail(self) -> None:
        for defect in ("snapshot", "rights", "future", "cutoff"):
            value = self.input()
            if defect == "snapshot":
                value["series"]["BTCUSDT"]["snapshot_sha256"] = "a" * 64
            if defect == "rights":
                value["source_use_sha256"] = "a" * 64
            if defect == "future":
                value["series"]["BTCUSDT"]["closes"].append("100.0")
            if defect == "cutoff":
                value["series"]["ETHUSDT"]["start_ns"] += HOUR
            data = canonical_json_bytes(value)
            with self.assertRaises(ValueError):
                validate_input(data)


if __name__ == "__main__":
    unittest.main()
