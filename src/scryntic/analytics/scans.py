"""Primitive result oracle and fixed long-horizon selection, without native imports."""

from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import validate_scan_rows

HOUR_NS = 3600 * 10**9


def compare_rows(rows: object, fixture: dict[str, Any]) -> list[dict[str, Any]]:
    checked = validate_scan_rows(rows, evolved=True)
    if checked != fixture["expected_rows"]:
        raise ValueError("Scan semantic parity mismatch")
    return checked


def horizon_inputs(
    rows: list[dict[str, Any]], *, horizons: tuple[int, ...] = (1, 24, 168, 672)
) -> dict[str, Any]:
    """A fixed synthetic workload, not a new forecast/availability policy."""
    index = {r["start_ns"]: r for r in rows}
    result = {}
    for horizon in horizons:
        paired = []
        exclusions = []
        missing = invalid = 0
        with localcontext() as context:
            context.prec = 28
            for row in rows:
                endpoint = index.get(row["start_ns"] + horizon * HOUR_NS)
                if endpoint is None:
                    missing += 1
                    exclusions.append([row["semantic_revision"], "missing-endpoint"])
                elif not all(
                    r["finalized"] and not r["quality_flags"] for r in (row, endpoint)
                ):
                    invalid += 1
                    exclusions.append([row["semantic_revision"], "invalid"])
                else:
                    paired.append(
                        [
                            row["semantic_revision"],
                            endpoint["semantic_revision"],
                            str(Decimal(endpoint["close"]) - Decimal(row["close"])),
                        ]
                    )
        paired_body: dict[str, Any] = {"pairs": paired}
        excluded_body: dict[str, Any] = {"exclusions": exclusions}
        result[str(horizon)] = {
            "eligible": len(paired),
            "excluded": missing + invalid,
            "missing_endpoint": missing,
            "invalid": invalid,
            "paired_loss_sha256": sha256(canonical_json_bytes(paired_body)).hexdigest(),
            "exclusions_sha256": sha256(
                canonical_json_bytes(excluded_body)
            ).hexdigest(),
        }
    return result
