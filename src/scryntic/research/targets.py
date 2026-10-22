"""Bounded PI02 duration targets over finalized hourly bar versions.

An origin is decided using only information available at its issue cutoff.
Outcome extraction is a separate operation and never upgrades reconstructed
archive timestamps into observed availability evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.validation import digest, integer

HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS
ISSUE_DELAY_NS = 60_000_000_000
HORIZONS_SECONDS = (3600, 86400, 604800, 2419200)
MAX_HOURLY_ROWS = 120_000


def _key(body: dict[str, Any]) -> str:
    return sha256(canonical_json_bytes(body)).hexdigest()


@dataclass(frozen=True, slots=True)
class SeriesKey:
    source_id: str
    venue: str
    category: str
    instrument: str
    snapshot_sha256: str
    availability_plane: str

    def __post_init__(self) -> None:
        for value in (self.source_id, self.venue, self.category, self.instrument):
            if type(value) is not str or not value or len(value) > 128:
                raise ValueError("Invalid series identity")
        digest(self.snapshot_sha256)
        if self.availability_plane not in ("historical-reconstruction", "as-observed"):
            raise ValueError("Unqualified availability plane")


@dataclass(frozen=True, slots=True)
class HourlyBar:
    start_ns: int
    close: Decimal
    raw_sha256: str
    known_at_ns: int | None = None
    revision: int = 0
    quality_ok: bool = True
    final: bool = True

    def __post_init__(self) -> None:
        integer(self.start_ns, 0)
        if self.start_ns % HOUR_NS:
            raise ValueError("Hourly bar must be UTC aligned")
        if type(self.close) is not Decimal or not self.close.is_finite():
            raise ValueError("Invalid finite close")
        digest(self.raw_sha256)
        if self.known_at_ns is not None:
            integer(self.known_at_ns, self.start_ns + HOUR_NS)
        integer(self.revision, 0)
        if type(self.quality_ok) is not bool or type(self.final) is not bool:
            raise TypeError("Finality and time quality must be explicit")


@dataclass(frozen=True, slots=True)
class ResampledBar:
    start_ns: int
    interval_ns: int
    close: Decimal
    known_at_ns: int | None
    raw_lineage_sha256: str


@dataclass(frozen=True, slots=True)
class ResampleResult:
    bars: tuple[ResampledBar, ...]
    exclusions: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class OriginRecord:
    origin_key: str
    series_identity_key: str
    input_version_key: str | None
    recipe_sha256: str | None
    feature_snapshot_sha256: str | None
    origin_end_ns: int
    horizon_seconds: int
    issue_cutoff_ns: int
    origin_close: Decimal | None
    known_at_ns: int | None
    evidence_class: str
    eligible: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    origin_key: str
    version_key: str | None
    endpoint_ns: int
    value: Decimal | None
    matured_at_ns: int | None
    evidence_class: str
    raw_lineage_sha256: str | None
    reason: str | None


class HourlySeries:
    """Immutable hourly snapshot with as-of revision selection and fixed bounds."""

    def __init__(self, key: SeriesKey, bars: list[HourlyBar]):
        if type(key) is not SeriesKey or type(bars) is not list:
            raise TypeError("Expected series key and hourly bars")
        if not bars or len(bars) > MAX_HOURLY_ROWS:
            raise ValueError("Empty or unbounded hourly input")
        versions: dict[int, list[HourlyBar]] = {}
        previous: tuple[int, int] | None = None
        for bar in bars:
            if type(bar) is not HourlyBar:
                raise TypeError("Invalid hourly bar")
            marker = (bar.start_ns, bar.revision)
            if previous is not None and marker <= previous:
                raise ValueError("Hourly input must be ordered with distinct revisions")
            previous = marker
            if key.availability_plane == "historical-reconstruction":
                if bar.known_at_ns is not None or bar.start_ns in versions:
                    raise ValueError(
                        "Historical revision or availability is unqualified"
                    )
            elif bar.known_at_ns is None:
                raise ValueError("Observed revision needs recorded availability")
            prior = versions.get(bar.start_ns)
            if prior and bar.known_at_ns is not None:
                earlier = prior[-1].known_at_ns
                if earlier is None or bar.known_at_ns <= earlier:
                    raise ValueError(
                        "Observed revisions need increasing knowledge time"
                    )
            versions.setdefault(bar.start_ns, []).append(bar)
        self.key = key
        self._series_identity_key = _key(
            {
                "source_id": key.source_id,
                "venue": key.venue,
                "category": key.category,
                "instrument": key.instrument,
                "availability_plane": key.availability_plane,
            }
        )
        self._versions = {start: tuple(items) for start, items in versions.items()}
        self.first_start_ns = bars[0].start_ns
        self.last_end_ns = bars[-1].start_ns + HOUR_NS
        self.row_count = len(bars)

    def _select(
        self, start_ns: int, as_of_ns: int | None
    ) -> tuple[HourlyBar | None, str | None]:
        versions = self._versions.get(start_ns)
        if versions is None:
            return None, "missing-constituent"
        if self.key.availability_plane == "as-observed":
            if as_of_ns is None:
                raise ValueError("Observed selection needs an as-of cutoff")
            selected = [
                bar
                for bar in versions
                if bar.known_at_ns is not None and bar.known_at_ns <= as_of_ns
            ]
            if not selected:
                return None, "not-yet-available"
            bar = selected[-1]
        else:
            bar = versions[0]
        if not bar.final:
            return None, "not-final"
        if not bar.quality_ok:
            return None, "time-quality"
        if bar.close <= 0:
            return None, "nonpositive-price"
        return bar, None

    def resample(
        self,
        start_ns: int,
        end_ns: int,
        as_of_ns: int | None,
        *,
        interval_ns: int = DAY_NS,
    ) -> ResampleResult:
        """Return only complete UTC-aligned buckets; never fill absent hours."""
        integer(start_ns, 0)
        integer(end_ns, 1)
        integer(interval_ns, HOUR_NS)
        if (
            interval_ns % HOUR_NS
            or interval_ns > 28 * DAY_NS
            or start_ns % interval_ns
            or end_ns % interval_ns
            or end_ns <= start_ns
            or (end_ns - start_ns) // interval_ns > MAX_HOURLY_ROWS
        ):
            raise ValueError("Invalid bounded UTC resampling range")
        if self.key.availability_plane == "as-observed":
            if as_of_ns is None:
                raise ValueError("Observed resampling needs as-of cutoff")
            integer(as_of_ns, 0)
        bars: list[ResampledBar] = []
        exclusions: list[tuple[int, str]] = []
        for bucket in range(start_ns, end_ns, interval_ns):
            selected: list[HourlyBar] = []
            for start in range(bucket, bucket + interval_ns, HOUR_NS):
                bar, reason = self._select(start, as_of_ns)
                if bar is None:
                    exclusions.append((bucket, reason or "missing-constituent"))
                    break
                selected.append(bar)
            else:
                known = (
                    max(
                        bar.known_at_ns
                        for bar in selected
                        if bar.known_at_ns is not None
                    )
                    if self.key.availability_plane == "as-observed"
                    else None
                )
                bars.append(
                    ResampledBar(
                        start_ns=bucket,
                        interval_ns=interval_ns,
                        close=selected[-1].close,
                        known_at_ns=known,
                        raw_lineage_sha256=_key(
                            {
                                "bucket": bucket,
                                "constituents": [bar.raw_sha256 for bar in selected],
                            }
                        ),
                    )
                )
        return ResampleResult(tuple(bars), tuple(exclusions))

    def origin(
        self,
        origin_end_ns: int,
        horizon_seconds: int,
        policy_sha256: str,
        *,
        lookback_hours: int = 24,
    ) -> OriginRecord:
        """Decide issue eligibility without reading any post-origin bar."""
        integer(origin_end_ns, HOUR_NS)
        digest(policy_sha256)
        integer(lookback_hours, 1)
        if (
            origin_end_ns % HOUR_NS
            or horizon_seconds not in HORIZONS_SECONDS
            or lookback_hours > 672
        ):
            raise ValueError("Unsupported PI02 origin or horizon")
        cutoff = origin_end_ns + ISSUE_DELAY_NS
        identity = {
            "source_id": self.key.source_id,
            "venue": self.key.venue,
            "category": self.key.category,
            "instrument": self.key.instrument,
            "policy_sha256": policy_sha256,
            "origin_end_ns": origin_end_ns,
            "horizon_seconds": horizon_seconds,
            "price_basis": "last-trade-close",
            "availability_plane": self.key.availability_plane,
        }
        origin_key = _key(identity)
        selected: list[HourlyBar] = []
        for start in range(
            origin_end_ns - lookback_hours * HOUR_NS, origin_end_ns, HOUR_NS
        ):
            bar, reason = self._select(start, cutoff)
            if bar is None:
                return OriginRecord(
                    origin_key=origin_key,
                    series_identity_key=self._series_identity_key,
                    input_version_key=None,
                    recipe_sha256=None,
                    feature_snapshot_sha256=None,
                    origin_end_ns=origin_end_ns,
                    horizon_seconds=horizon_seconds,
                    issue_cutoff_ns=cutoff,
                    origin_close=None,
                    known_at_ns=None,
                    evidence_class=self.key.availability_plane,
                    eligible=False,
                    reason=reason,
                )
            selected.append(bar)
        latest = selected[-1]
        recipe_sha256 = _key(
            {"operation": "pi02-close-history-v1", "lookback_hours": lookback_hours}
        )
        feature_snapshot_sha256 = _key(
            {
                "snapshot_sha256": self.key.snapshot_sha256,
                "constituents": [bar.raw_sha256 for bar in selected],
            }
        )
        return OriginRecord(
            origin_key=origin_key,
            series_identity_key=self._series_identity_key,
            input_version_key=_key(
                {
                    "origin_key": origin_key,
                    "recipe_sha256": recipe_sha256,
                    "feature_snapshot_sha256": feature_snapshot_sha256,
                }
            ),
            recipe_sha256=recipe_sha256,
            feature_snapshot_sha256=feature_snapshot_sha256,
            origin_end_ns=origin_end_ns,
            horizon_seconds=horizon_seconds,
            issue_cutoff_ns=cutoff,
            origin_close=latest.close,
            known_at_ns=(
                max(bar.known_at_ns for bar in selected if bar.known_at_ns is not None)
                if self.key.availability_plane == "as-observed"
                else None
            ),
            evidence_class=self.key.availability_plane,
            eligible=True,
            reason=None,
        )

    def outcome(
        self, origin: OriginRecord, score_as_of_ns: int | None = None
    ) -> OutcomeRecord:
        """Resolve a matured duration label without changing its pre-outcome key."""
        if (
            type(origin) is not OriginRecord
            or not origin.eligible
            or origin.origin_close is None
        ):
            raise ValueError("Cannot score ineligible origin")
        if origin.series_identity_key != self._series_identity_key:
            raise ValueError("Origin belongs to another series")
        endpoint = origin.origin_end_ns + origin.horizon_seconds * 1_000_000_000
        if self.key.availability_plane != origin.evidence_class:
            raise ValueError("Origin evidence class differs from series")
        if self.key.availability_plane == "as-observed":
            if score_as_of_ns is None:
                raise ValueError("Observed scoring needs an as-of cutoff")
            integer(score_as_of_ns, endpoint + ISSUE_DELAY_NS)
        selected: list[HourlyBar] = []
        for start in range(origin.origin_end_ns, endpoint, HOUR_NS):
            bar, reason = self._select(start, score_as_of_ns)
            if bar is None:
                return OutcomeRecord(
                    origin.origin_key,
                    None,
                    endpoint,
                    None,
                    None,
                    origin.evidence_class,
                    None,
                    reason,
                )
            selected.append(bar)
        lineage = _key(
            {"endpoint": endpoint, "constituents": [bar.raw_sha256 for bar in selected]}
        )
        value = (selected[-1].close / origin.origin_close).ln()
        return OutcomeRecord(
            origin_key=origin.origin_key,
            version_key=_key(
                {
                    "origin_key": origin.origin_key,
                    "input_version_key": origin.input_version_key,
                    "outcome_lineage_sha256": lineage,
                    "target_value": str(value),
                }
            ),
            endpoint_ns=endpoint,
            value=value,
            matured_at_ns=(
                max(bar.known_at_ns for bar in selected if bar.known_at_ns is not None)
                if self.key.availability_plane == "as-observed"
                else None
            ),
            evidence_class=origin.evidence_class,
            raw_lineage_sha256=lineage,
            reason=None,
        )
