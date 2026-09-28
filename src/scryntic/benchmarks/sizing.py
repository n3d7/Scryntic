"""Configurable capacity arithmetic; this neither prunes nor controls intake."""

import math
from dataclasses import dataclass, fields


def _number(value: float, minimum: float = 0) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError("Expected a finite nonnegative sizing value")


def _count(value: int, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("Expected an integer sizing count")


@dataclass(frozen=True, slots=True)
class FamilyRate:
    name: str
    instruments: int
    envelopes_per_instrument_second: float
    encoded_record_bytes: float
    logical_record_bytes: float
    spool_record_bytes: float
    derived_record_bytes: float

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("Expected family name")
        _count(self.instruments)
        for field in fields(self)[2:]:
            _number(getattr(self, field.name))

    @property
    def rate(self) -> float:
        return self.instruments * self.envelopes_per_instrument_second


@dataclass(frozen=True, slots=True)
class SizingConfig:
    families: tuple[FamilyRate, ...]
    horizon_days: float = 90
    # F10 does not prune the F05 spool: include its full retained horizon.
    spool_days: float = 90
    peak_records_per_second: float = 100
    burst_seconds: float = 3
    measured_service_records_per_second: float = 1000
    service_fraction: float = 0.5
    queue_seconds: float = 2
    segment_records: int = 256
    max_payload_bytes: int = 65536
    max_encoded_segment_bytes: int = 4 * 1024 * 1024
    max_decoded_segment_bytes: int = 8 * 1024 * 1024
    queue_memory_multiplier: float = 3
    manifest_catalog_bytes_per_segment: int = 24576
    staging_segments: int = 2
    fixed_reserve_bytes: int = 512 * 1024 * 1024
    free_disk_fraction: float = 0.3

    def __post_init__(self) -> None:
        if not self.families or any(
            not isinstance(f, FamilyRate) for f in self.families
        ):
            raise ValueError("Expected configured sizing families")
        if len({f.name for f in self.families}) != len(self.families):
            raise ValueError("Duplicate sizing family")
        for name in (
            "horizon_days",
            "spool_days",
            "peak_records_per_second",
            "burst_seconds",
            "measured_service_records_per_second",
            "queue_seconds",
            "queue_memory_multiplier",
        ):
            _number(getattr(self, name))
        for name in (
            "segment_records",
            "max_payload_bytes",
            "max_encoded_segment_bytes",
            "max_decoded_segment_bytes",
            "manifest_catalog_bytes_per_segment",
            "staging_segments",
            "fixed_reserve_bytes",
        ):
            _count(getattr(self, name))
        _number(self.service_fraction)
        _number(self.free_disk_fraction)
        if not 0 < self.service_fraction <= 1 or not 0 <= self.free_disk_fraction < 1:
            raise ValueError("Invalid sizing reserve fraction")
        if (
            self.measured_service_records_per_second <= 0
            or self.queue_memory_multiplier < 1
        ):
            raise ValueError("Invalid sizing service or memory reserve")
        if self.spool_days < self.horizon_days:
            raise ValueError("Unpruned spool must cover the modeled horizon")


def size_envelope(config: SizingConfig) -> dict[str, int | float | bool]:
    """Estimate envelope counts/copies and burst backlog with explicit reserves.

    Rates count envelopes, not trades or book levels inside an envelope. Average
    segment fill is assumed; lower fill/more partitions needs a larger allowance.
    max_decoded_bytes is canonical evidence size, not an RSS cap.
    """
    seconds = config.horizon_days * 86400
    spool_seconds = config.spool_days * 86400
    records = sum(math.ceil(f.rate * seconds) for f in config.families)
    segments = sum(
        math.ceil(math.ceil(f.rate * seconds) / config.segment_records)
        for f in config.families
    )
    archive = sum(
        math.ceil(f.rate * seconds * f.encoded_record_bytes) for f in config.families
    )
    spool = sum(
        math.ceil(f.rate * spool_seconds * f.spool_record_bytes)
        for f in config.families
    )
    derived = sum(
        math.ceil(f.rate * seconds * f.derived_record_bytes) for f in config.families
    )
    metadata = segments * config.manifest_catalog_bytes_per_segment
    retained = archive + spool + derived + metadata
    # Reserve simultaneous raw + derived staging at the configured encoded bound.
    staging = 2 * config.staging_segments * config.max_encoded_segment_bytes
    required = math.ceil(
        (retained + staging + config.fixed_reserve_bytes)
        / (1 - config.free_disk_fraction)
    )
    rate = sum(f.rate for f in config.families)
    service = config.measured_service_records_per_second * config.service_fraction
    peak = max(config.peak_records_per_second, rate)
    backlog = math.ceil(max(0, peak - service) * config.burst_seconds)
    queue_records = max(1, math.ceil(peak * config.queue_seconds), backlog)
    payload_budget = (
        queue_records + config.segment_records + 1
    ) * config.max_payload_bytes
    return {
        "horizon_days": config.horizon_days,
        "records": records,
        "segments": segments,
        "archive_bytes": archive,
        "spool_bytes": spool,
        "derived_bytes": derived,
        "manifest_catalog_bytes": metadata,
        "retained_bytes": retained,
        "staging_reserve_bytes": staging,
        "required_disk_bytes": required,
        "sustained_records_per_second": rate,
        "service_records_per_second": service,
        "sustained_utilization": rate / service,
        "sustained_within_budget": rate < service,
        "burst_backlog_records": backlog,
        "queue_records": queue_records,
        "queue_payload_bytes": payload_budget,
        "queue_memory_bytes": math.ceil(
            payload_budget * config.queue_memory_multiplier
        ),
        "segment_expected_logical_bytes": math.ceil(
            config.segment_records
            * max(f.logical_record_bytes for f in config.families)
        ),
        "segment_decoded_within_budget": config.segment_records
        * max(f.logical_record_bytes for f in config.families)
        <= config.max_decoded_segment_bytes,
        "segment_payload_worst_case_bytes": config.segment_records
        * config.max_payload_bytes,
    }


def config_from_dict(value: dict[str, object]) -> SizingConfig:
    """Read a closed configuration schema; never import names supplied by data."""
    settings = dict(value)
    family_values = settings.pop("families")
    if type(family_values) is not list:
        raise ValueError("Expected sizing families")
    families = []
    for family in family_values:
        if type(family) is not dict:
            raise ValueError("Expected sizing family object")
        families.append(FamilyRate(**family))
    return SizingConfig(families=tuple(families), **settings)  # type: ignore[arg-type]
