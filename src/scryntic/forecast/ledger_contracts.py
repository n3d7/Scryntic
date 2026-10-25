"""PI04 identities and closed experimental admission; no live-data grant."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import digest, identifier, integer
from scryntic.research.targets import HORIZONS_SECONDS, HOUR_NS, ISSUE_DELAY_NS

FINAL_START_NS = 1793491200 * 10**9
FINAL_END_NS = 1893456000 * 10**9
DEVELOPMENT_END_NS = 1790812800 * 10**9
SHADOW_START_NS = 1792800000 * 10**9
FORWARD_SOURCE = "binance-usdm-public-hourly-v1"
FORWARD_CONTRACT_SHA256 = (
    "9b885c4de13b59aa1d38c82165323b69336d3a46fd423aaf26e3d22feb4d04e7"
)
BASELINE = {
    "algorithm": "zero-log-return-v1",
    "point_statistic": "deterministic-point",
    "point": "0",
    "training": False,
    "probabilities": False,
}


def identity(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


BASELINE_SHA256 = identity(BASELINE)
RUNTIME_SHA256 = identity(
    {"python": platform.python_version(), "adapter": "pi04-zero-v1"}
)


def code_identity() -> str:
    paths = (Path(__file__), Path(__file__).with_name("inference.py"))
    return identity({p.name: sha256(p.read_bytes()).hexdigest() for p in paths})


@dataclass(frozen=True, slots=True)
class ForwardAdmission:
    """Explicit trusted-owner grant; never supplied by an inference worker."""

    contract_sha256: str = FORWARD_CONTRACT_SHA256

    def __post_init__(self) -> None:
        if self.contract_sha256 != FORWARD_CONTRACT_SHA256:
            raise ValueError("Unregistered PI04 forward supplement")


def decoded(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate PI04 key")
            result[key] = value
        return result

    if type(data) is not bytes or not 0 < len(data) <= 65536:
        raise ValueError("PI04 payload bound")
    value = json.loads(data, object_pairs_hook=pairs)
    if type(value) is not dict or canonical_json_bytes(value) != data:
        raise ValueError("Noncanonical PI04 object")
    return value


def permitted_interval(start: int, end: int, evidence: str) -> None:
    integer(start, HOUR_NS)
    integer(end, start + 1)
    if end >= 2**63 or (start < FINAL_END_NS and end >= FINAL_START_NS):
        raise ValueError("PI04 final reservation is sealed")
    if evidence != "synthetic-fixture" and end >= DEVELOPMENT_END_NS:
        raise ValueError("PI04 real-data development window is closed")


@dataclass(frozen=True, slots=True)
class Stream:
    stream_id: str
    evidence_class: str
    source_id: str
    baseline_sha256: str = BASELINE_SHA256
    runtime_sha256: str = RUNTIME_SHA256
    code_sha256: str = field(default_factory=code_identity)

    def __post_init__(self) -> None:
        identifier(self.stream_id)
        if self.evidence_class not in (
            "synthetic-fixture",
            "historical-reconstruction",
            "simulated-as-observed",
            "backcast",
            "forward-shadow",
        ):
            raise ValueError("PI04 live source is not admitted")
        allowed = (
            {FORWARD_SOURCE}
            if self.evidence_class == "forward-shadow"
            else {"synthetic-hourly"}
            if self.evidence_class == "synthetic-fixture"
            else {"synthetic-hourly", "binance-vision-hourly"}
        )
        if self.source_id not in allowed or self.baseline_sha256 != BASELINE_SHA256:
            raise ValueError("Unadmitted PI04 source or baseline")
        digest(self.runtime_sha256)
        digest(self.code_sha256)


@dataclass(frozen=True, slots=True)
class ForecastSlot:
    stream: Stream
    venue: str
    category: str
    instrument: str
    origin_ns: int
    horizon_seconds: int
    target: str = "log-last-trade-close-v1"

    def __post_init__(self) -> None:
        self.stream.__post_init__()
        for value in (self.venue, self.category, self.instrument):
            identifier(value)
        integer(self.origin_ns, HOUR_NS)
        if self.origin_ns % HOUR_NS or self.horizon_seconds not in HORIZONS_SECONDS:
            raise ValueError("Unregistered PI04 origin or horizon")
        if self.horizon_seconds != 3600 and self.origin_ns % (24 * HOUR_NS):
            raise ValueError("Long PI04 horizons require the UTC daily grid")
        if self.target != "log-last-trade-close-v1":
            raise ValueError("Unregistered PI04 target")
        if self.stream.evidence_class == "forward-shadow":
            if (
                not SHADOW_START_NS <= self.origin_ns < FINAL_START_NS
                or self.venue != "Binance"
                or self.category != "USD-M-futures"
                or self.instrument not in ("BTCUSDT", "ETHUSDT")
            ):
                raise ValueError(
                    "PI04 forward origin/native instrument outside supplement"
                )
        else:
            permitted_interval(
                self.origin_ns, self.endpoint_ns, self.stream.evidence_class
            )

    @property
    def endpoint_ns(self) -> int:
        return self.origin_ns + self.horizon_seconds * 10**9

    @property
    def deadline_ns(self) -> int:
        return self.origin_ns + ISSUE_DELAY_NS

    @property
    def sha256(self) -> str:
        return identity(
            {
                "stream_id": self.stream.stream_id,
                "venue": self.venue,
                "category": self.category,
                "instrument": self.instrument,
                "origin_ns": self.origin_ns,
                "horizon_seconds": self.horizon_seconds,
                "target": self.target,
            }
        )


@dataclass(frozen=True, slots=True)
class ForecastContent:
    slot: ForecastSlot
    input_sha256: str
    reference_sha256: str
    reference_close: Decimal
    latest_available_ns: int
    point: Decimal | None
    reason: str | None = None
    point_statistic: str = "deterministic-point"

    def __post_init__(self) -> None:
        self.slot.__post_init__()
        digest(self.input_sha256)
        digest(self.reference_sha256)
        if (
            type(self.reference_close) is not Decimal
            or not self.reference_close.is_finite()
            or self.reference_close <= 0
        ):
            raise ValueError("Invalid PI04 reference close")
        integer(self.latest_available_ns, 0)
        if self.latest_available_ns > self.slot.deadline_ns:
            raise ValueError("PI04 input was unavailable at the cutoff")
        if self.point is not None and (
            type(self.point) is not Decimal or not self.point.is_finite()
        ):
            raise ValueError("Invalid PI04 point")
        if self.point is not None and self.point != 0:
            raise ValueError("PI04 admits only the frozen zero-return baseline")
        if self.point_statistic != "deterministic-point" or (
            (self.point is None) != (self.reason is not None)
        ):
            raise ValueError("PI04 abstention and point semantics differ")
        if self.reason is not None:
            identifier(self.reason)

    def body(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "slot": asdict(self.slot),
            "input_sha256": self.input_sha256,
            "reference_sha256": self.reference_sha256,
            "reference_close": str(self.reference_close),
            "latest_available_ns": self.latest_available_ns,
            "point": None if self.point is None else str(self.point),
            "reason": self.reason,
            "point_statistic": self.point_statistic,
        }

    @property
    def sha256(self) -> str:
        return identity(self.body())

    @classmethod
    def from_body(cls, value: dict[str, Any]) -> ForecastContent:
        raw = dict(value)
        slot = dict(raw.pop("slot"))
        slot["stream"] = Stream(**slot["stream"])
        raw["reference_close"] = Decimal(raw["reference_close"])
        raw["point"] = None if raw["point"] is None else Decimal(raw["point"])
        result = cls(ForecastSlot(**slot), **raw)
        if result.body() != value:
            raise ValueError("Invalid PI04 content encoding")
        return result


def timeliness(before: ClockSample, after: ClockSample, slot: ForecastSlot) -> str:
    for value in (before, after):
        value.__post_init__()
        q = value.quality
        if (
            q.status != "healthy"
            or q.uncertainty_ns is None
            or q.offset_ns is None
            or q.evidence_age_ns is None
            or q.evidence_age_ns > 120 * 10**9
            or q.uncertainty_ns < abs(q.offset_ns)
        ):
            return "unknown"
    if (
        before.session_id != after.session_id
        or before.quality.epoch != after.quality.epoch
        or after.wall_time_ns < before.wall_time_ns
        or after.monotonic_ns < before.monotonic_ns
    ):
        return "clock-discontinuity"
    assert before.quality.uncertainty_ns is not None
    assert after.quality.uncertainty_ns is not None
    if (
        abs(
            (after.wall_time_ns - before.wall_time_ns)
            - (after.monotonic_ns - before.monotonic_ns)
        )
        > before.quality.uncertainty_ns + after.quality.uncertainty_ns
    ):
        return "clock-discontinuity"
    lower = after.wall_time_ns - after.quality.uncertainty_ns
    upper = after.wall_time_ns + after.quality.uncertainty_ns
    if (
        lower >= slot.origin_ns
        and upper <= slot.deadline_ns
        and upper < slot.endpoint_ns
    ):
        return "on-time"
    return "late" if lower > slot.deadline_ns else "uncertain"
