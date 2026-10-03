"""Candle replay over validated primitives; native parsing stays in F18 workers."""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.clock.policy import observed_by, time_interval
from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.protocol import clock_from, validate_rows
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy, _coverage_status
from scryntic.dataset.schemas import F18_RECIPE_SCHEMA, schema_projection
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import CandleKey
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import integer
from scryntic.replay.contracts import ReplayConfig, ReplayFeatures, Series

MAX_REPLAY_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    value: Decimal
    end_ns: int
    available_ns: int
    flags: tuple[str, ...]


class CandleReplayReader:
    """A fixed snapshot never invents an earlier revision of a selected candle."""

    def __init__(
        self, manifest: dict[str, Any], rows: list[dict[str, Any]], config: ReplayConfig
    ) -> None:
        self.config = config
        self.manifest = deepcopy(manifest)
        self.rows = deepcopy(rows)
        self.limits = ClockLimits(**manifest["clock_limits"])
        self.policy = RecipePolicy(**manifest["policy"])
        if (
            manifest["recipe"] != schema_projection(F18_RECIPE_SCHEMA)
            or self.policy.label_horizon_steps == 0
            or len(rows) != manifest["row_count"]
            or len(rows) != len(manifest["rows"])
            or len(rows) > MAX_REPLAY_ROWS
            or config.decision_uncertainty_ns > self.limits.max_uncertainty_ns
        ):
            raise ValueError("Replay requires bounded F18 labeled candle data")
        self._claims = self._coverage_claims()
        self._receipts: list[ClockSample] = []
        self._fingerprints: dict[str, int] = {}
        self._keys: dict[tuple[Series, int], int] = {}
        for offset in range(0, len(rows), 128):
            validate_rows(self.rows[offset : offset + 128], evolved=True)
        for i, row in enumerate(self.rows):
            self._validate_decimal128(row)
            self._validate_source(i, row)
        self._derived = {
            item["row_index"]: item for item in self.manifest["derived_lineage"]
        }
        if set(self._derived) != set(range(len(rows))) or len(
            self.manifest["derived_lineage"]
        ) != len(rows):
            raise ValueError("Incomplete derived lineage")
        self._chains: dict[tuple[int, str], tuple[int, ...] | None] = {}
        for i in range(len(rows)):
            self._validate_chain(i, "lag", self.policy.lag_steps, -1)
            self._validate_chain(i, "label", self.policy.label_horizon_steps, 1)

    @staticmethod
    def _validate_decimal128(row: dict[str, Any]) -> None:
        for name in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "lag_close",
            "label_close",
        ):
            if row[name] is None:
                continue
            value = Decimal(row[name])
            parts = value.as_tuple()
            exponent = parts.exponent
            if (
                not isinstance(exponent, int)
                or not -18 <= exponent <= 20
                or len(parts.digits) > 38
                or value.adjusted() >= 20
            ):
                raise ValueError("Replay price exceeds decimal128(38,18)")

    def _coverage_claims(self) -> tuple[CoverageClaim, ...]:
        claims = []
        for item in self.manifest["coverage_claims"]:
            sample = clock_from(item["detected"])
            if sample is None:
                raise ValueError("Missing coverage detection clock")
            claims.append(
                CoverageClaim(
                    InstrumentId(**item["instrument"]),
                    item["interval_ns"],
                    item["start_ns"],
                    item["end_ns"],
                    item["status"],
                    sample,
                )
            )
        return tuple(claims)

    def series(self, index: int) -> Series:
        row = self.rows[index]
        return row["venue"], row["category"], row["symbol"], row["interval_ns"]

    def _validate_source(self, i: int, row: dict[str, Any]) -> None:
        lineage = self.manifest["rows"][i]
        selected = lineage["selected"]
        value = selected["input"]
        semantics = value["semantics"]
        key = semantics["key"]
        fingerprint = sha256(canonical_json_bytes(value)).hexdigest()
        if (
            lineage["index"] != i
            or fingerprint != selected["input_fingerprint"]
            or fingerprint in self._fingerprints
            or self.series(i)
            != (
                key["instrument"]["venue"],
                key["instrument"]["category"],
                key["instrument"]["symbol"],
                key["interval_ns"],
            )
            or row["start_ns"] != key["start_ns"]
            or row["finalized"] != semantics["finalized"]
            or row["quality_flags"] != semantics["quality_flags"]
            or any(
                Decimal(row[name]) != Decimal(semantics[name])
                for name in ("open", "high", "low", "close", "volume")
            )
        ):
            raise ValueError("Replay source lineage mismatch")
        integer(row["interval_ns"], 1)
        sample = clock_from(value["raw"]["receipt"])
        if sample is None or sample.wall_time_ns != row["receipt_wall_time_ns"]:
            raise ValueError("Replay receipt lineage mismatch")
        candle_key = (self.series(i), row["start_ns"])
        if candle_key in self._keys:
            raise ValueError("Duplicate replay candle key")
        self._keys[candle_key] = i
        self._fingerprints[fingerprint] = i
        self._receipts.append(sample)

    def _expected_chain(
        self, i: int, steps: int, direction: int, *, finalized: bool
    ) -> tuple[list[int], str | None]:
        row = self.rows[i]
        expected: list[int] = []
        reason = None
        for step in range(1, steps + 1):
            index = self._keys.get(
                (
                    self.series(i),
                    row["start_ns"] + direction * step * row["interval_ns"],
                )
            )
            if index is None:
                reason = "gap-or-missing"
                break
            expected.append(index)
            if finalized and not self.rows[index]["finalized"]:
                reason = "not-finalized"
                break
        return expected, reason

    def _validate_chain(self, i: int, name: str, steps: int, direction: int) -> None:
        row, definition = self.rows[i], self._derived[i]
        if not steps:
            if row[f"{name}_close"] is not None:
                raise ValueError("Disabled derived value")
            self._chains[i, name] = ()
            return
        expected, reason = self._expected_chain(
            i, steps, direction, finalized=name == "label"
        )
        fingerprints = [
            self.manifest["rows"][index]["selected"]["input_fingerprint"]
            for index in expected
        ]
        if (
            definition[f"{name}_source_fingerprints"] != fingerprints
            or definition[f"{name}_missing_reason"] != reason
        ):
            raise ValueError("Invalid derived lineage")
        value = row[f"{name}_close"]
        if reason is not None:
            if value is not None or (
                name == "label" and row["label_end_ns"] is not None
            ):
                raise ValueError("Missing derived value must remain null")
            self._chains[i, name] = None
            return
        last = self.rows[expected[-1]]
        if value is None or Decimal(value) != Decimal(last["close"]):
            raise ValueError("Invalid derived value")
        if (
            name == "label"
            and row["label_end_ns"] != last["start_ns"] + last["interval_ns"]
        ):
            raise ValueError("Invalid derived label end")
        self._chains[i, name] = tuple(expected)

    def _availability(self, index: int) -> int | None:
        row = self.rows[index]
        closed = row["start_ns"] + row["interval_ns"]
        if self.config.mode == "historical-reconstruction":
            return int(closed)
        interval = time_interval(self._receipts[index], self.limits)
        return None if interval is None else max(closed, interval[1])

    def _quality(
        self, indexes: tuple[int, ...], clock: ClockSample
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        reasons: set[str] = set()
        flags: set[str] = set()
        claims = self._claims
        if self.config.mode == "as-observed":
            claims = tuple(
                claim
                for claim in claims
                if observed_by(claim.detected, clock, self.limits)
            )
        for index in indexes:
            row = self.rows[index]
            flags.update(row["quality_flags"])
            if time_interval(self._receipts[index], self.limits) is None:
                flags.add("time-quality")
                if (
                    self.config.time_quality == "exclude"
                    or self.config.mode == "as-observed"
                ):
                    reasons.add("time-quality")
            key = CandleKey(
                instrument=InstrumentId(*self.series(index)[:3]),
                interval_ns=row["interval_ns"],
                start_ns=row["start_ns"],
            )
            status = _coverage_status(key, claims)
            if status != "complete":
                flags.add(f"coverage-{status}")
                if self.config.coverage == "exclude":
                    reasons.add("coverage")
        return tuple(sorted(reasons)), tuple(sorted(flags))

    def feature_result(
        self, index: int, clock: ClockSample
    ) -> tuple[ReplayFeatures | None, tuple[str, ...]]:
        decision = time_interval(clock, self.limits)
        if decision is None:
            raise ValueError("Replay decision requires bounded uncertainty")
        if not self.rows[index]["finalized"]:
            return None, ("not-finalized",)
        lag = self._chains[index, "lag"]
        if lag is None:
            return None, ("lag-gap-or-missing",)
        if any(not self.rows[i]["finalized"] for i in lag):
            return None, ("lag-not-finalized",)
        dependencies = (index, *lag)
        reasons, flags = self._quality(dependencies, clock)
        unavailable = any(
            (available := self._availability(i)) is None or available > decision[0]
            for i in dependencies
        )
        if reasons or unavailable:
            return None, tuple(
                sorted({*reasons, *(("feature-unavailable",) if unavailable else ())})
            )
        values: tuple[Decimal, ...] = (Decimal(self.rows[index]["close"]),)
        if self.policy.lag_steps:
            values += (Decimal(self.rows[index]["lag_close"]),)
        return ReplayFeatures(
            index, self.series(index), clock.wall_time_ns, values, flags
        ), ()

    def features(self, index: int, clock: ClockSample) -> ReplayFeatures | None:
        return self.feature_result(index, clock)[0]

    def target_result(
        self, index: int, clock: ClockSample
    ) -> tuple[ReplayTarget | None, tuple[str, ...]]:
        decision = time_interval(clock, self.limits)
        if decision is None:
            raise ValueError("Replay scoring requires bounded uncertainty")
        chain = self._chains[index, "label"]
        if chain is None:
            return None, (f"label-{self._derived[index]['label_missing_reason']}",)
        availability = [self._availability(i) for i in chain]
        if any(value is None or value > decision[0] for value in availability):
            return None, ("label-unavailable",)
        reasons, flags = self._quality(chain, clock)
        if reasons:
            return None, tuple(f"label-{reason}" for reason in reasons)
        available_ns = max(value for value in availability if value is not None)
        return ReplayTarget(
            Decimal(self.rows[index]["label_close"]),
            self.rows[index]["label_end_ns"],
            available_ns,
            flags,
        ), ()

    def target(self, index: int, clock: ClockSample) -> ReplayTarget | None:
        return self.target_result(index, clock)[0]
