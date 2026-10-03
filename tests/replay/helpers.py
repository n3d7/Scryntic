"""Real F18 selection/derivation lineage, without file decoding in replay."""

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.recipes import RecipePolicy, derive_rows
from scryntic.dataset.schemas import F18_RECIPE_SCHEMA, schema_projection
from scryntic.dataset.selection import SourceInput, select_candles
from scryntic.dataset.snapshot import _lineage, _row
from tests.dataset.test_recipes import _received
from tests.dataset.test_selection import _source

START = 1_700_000_000_000_000_000
STEP = 60_000_000_000


def sources(count: int = 12) -> tuple[SourceInput, ...]:
    return tuple(
        _received(
            _source(
                i + 1,
                finalized=True,
                close=str(Decimal(100) + Decimal(i) / 100),
                start_ns=START + i * STEP,
            ),
            START + (i + 1) * STEP,
        )
        for i in range(count)
    )


def snapshot(
    inputs: tuple[SourceInput, ...] | None = None, *, lag: int = 0, horizon: int = 1
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    chosen = sources() if inputs is None else inputs
    selected = select_candles(chosen) if chosen else ()
    policy = RecipePolicy(lag_steps=lag, label_horizon_steps=horizon)
    rows, derived = derive_rows(selected, [_row(item) for item in selected], policy)
    for row in rows:
        for name in ("lag_close", "label_close", "label_end_ns"):
            row.setdefault(name, None)
        for name, value in row.items():
            if isinstance(value, Decimal):
                row[name] = str(value)
    manifest = {
        "recipe": schema_projection(F18_RECIPE_SCHEMA),
        "policy": policy.projection(),
        "clock_limits": asdict(ClockLimits()),
        "row_count": len(rows),
        "rows": [
            {"index": i, "selected": _lineage(item.selected)}
            for i, item in enumerate(selected)
        ],
        "derived_lineage": derived,
        "coverage_claims": [],
        "exclusions": [],
    }
    return manifest, rows
