"""Pure, explicit selection and gap-aware recipes for local candle snapshots."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal

from scryntic.archive.canonical import input_fingerprint
from scryntic.clock.policy import observed_by, time_interval
from scryntic.configuration.clock import ClockLimits
from scryntic.dataset.selection import (
    SelectedCandle,
    SelectionError,
    SourceInput,
    select_candles,
    strict_eligible,
)
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import CandleKey
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import integer


@dataclass(frozen=True, slots=True)
class RecipePolicy:
    mode: Literal["historical-reconstruction", "as-observed"] = (
        "historical-reconstruction"
    )
    revision: Literal["latest", "first"] = "latest"
    coverage: Literal["include", "exclude", "require-complete"] = "include"
    time_quality: Literal["include", "exclude", "require-healthy"] = "include"
    finalized_only: bool = False
    lag_steps: int = 0
    label_horizon_steps: int = 0

    def __post_init__(self) -> None:
        for value, choices in (
            (self.mode, ("historical-reconstruction", "as-observed")),
            (self.revision, ("latest", "first")),
            (self.coverage, ("include", "exclude", "require-complete")),
            (self.time_quality, ("include", "exclude", "require-healthy")),
        ):
            if value not in choices:
                raise ValueError("Unsupported recipe policy")
        if type(self.finalized_only) is not bool:
            raise TypeError("finalized_only must be a boolean")
        for steps in (self.lag_steps, self.label_horizon_steps):
            integer(steps, 0)
            if steps > 128:
                raise ValueError("Recipe steps exceed bounded maximum")

    def projection(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CoverageClaim:
    """Trusted local captured ledger evidence; callers pin this projection's hash."""

    instrument: InstrumentId
    interval_ns: int
    start_ns: int
    end_ns: int
    status: Literal["complete", "pending", "unknown", "unrecoverable"]
    detected: ClockSample

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("Expected InstrumentId")
        integer(self.interval_ns, 1)
        integer(self.start_ns)
        integer(self.end_ns)
        if self.end_ns <= self.start_ns:
            raise ValueError("Coverage range must be nonempty")
        if self.status not in ("complete", "pending", "unknown", "unrecoverable"):
            raise ValueError("Unsupported coverage status")
        if not isinstance(self.detected, ClockSample):
            raise TypeError("Expected coverage detection clock sample")

    def projection(self) -> dict[str, Any]:
        return asdict(self)


def _coverage_status(key: CandleKey, claims: tuple[CoverageClaim, ...]) -> str:
    end = key.start_ns + key.interval_ns
    overlapping = [
        claim
        for claim in claims
        if claim.instrument == key.instrument
        and claim.interval_ns == key.interval_ns
        and claim.start_ns < end
        and key.start_ns < claim.end_ns
    ]
    # Any unresolved overlapping evidence remains visible, even beside a repair.
    for status in ("unrecoverable", "unknown", "pending"):
        if any(claim.status == status for claim in overlapping):
            return status
    cursor = key.start_ns
    for claim in sorted(overlapping, key=lambda claim: (claim.start_ns, claim.end_ns)):
        if claim.start_ns > cursor:
            break
        cursor = max(cursor, claim.end_ns)
    return "complete" if cursor >= end else "unknown"


def _record(source: SourceInput, reason: str, **details: Any) -> dict[str, Any]:
    identity = source.value.raw.identity
    return {
        "producer": identity.producer,
        "epoch": identity.epoch,
        "offset": identity.offset,
        "input_fingerprint": input_fingerprint(source.value),
        "reason": reason,
        **details,
    }


def _observed_selection(
    sources: tuple[SourceInput, ...],
    policy: RecipePolicy,
    cutoff: ClockSample | None,
    limits: ClockLimits,
    coverage: tuple[CoverageClaim, ...],
) -> tuple[tuple[SelectedCandle, ...], tuple[CoverageClaim, ...], list[dict[str, Any]]]:
    """Validate full history before applying the bounded observation window."""
    selected = select_candles(sources)
    records: list[dict[str, Any]] = []
    claims = coverage
    if policy.mode == "as-observed":
        if cutoff is None or time_interval(cutoff, limits) is None:
            raise SelectionError("Strict recipe requires a healthy cutoff")
        eligible, excluded = strict_eligible(sources, cutoff, limits)
        records.extend(dict(item) for item in excluded)
        selected = select_candles(eligible)
        claims = tuple(
            claim for claim in coverage if observed_by(claim.detected, cutoff, limits)
        )
    elif cutoff is not None:
        raise SelectionError("Historical recipe cannot carry an observation cutoff")
    return selected, claims, records


def _retain_source(
    source: SourceInput,
    policy: RecipePolicy,
    limits: ClockLimits,
    claims: tuple[CoverageClaim, ...],
    records: list[dict[str, Any]],
) -> bool:
    """Apply finality, clock and coverage policies in their recorded order."""
    semantics = source.value.semantics
    assert semantics is not None  # select_candles validates this contract
    if policy.finalized_only and not semantics.finalized:
        records.append(_record(source, "not-finalized"))
        return False
    healthy = time_interval(source.value.raw.envelope.receipt, limits) is not None
    if not healthy:
        if policy.time_quality == "require-healthy":
            raise SelectionError("Recipe requires healthy time evidence")
        records.append(
            _record(source, "time-quality", excluded=policy.time_quality == "exclude")
        )
        if policy.time_quality == "exclude":
            return False
    status = _coverage_status(semantics.key, claims)
    if status != "complete":
        if policy.coverage == "require-complete":
            raise SelectionError("Recipe requires complete coverage")
        records.append(
            _record(
                source,
                "coverage",
                coverage_status=status,
                excluded=policy.coverage == "exclude",
            )
        )
        if policy.coverage == "exclude":
            return False
    return True


def apply_policy(
    sources: tuple[SourceInput, ...],
    policy: RecipePolicy,
    cutoff: ClockSample | None,
    limits: ClockLimits,
    coverage: tuple[CoverageClaim, ...] = (),
) -> tuple[tuple[SelectedCandle, ...], tuple[dict[str, Any], ...]]:
    """Validate full history before filtering; never silently suppress conflicts.

    Strict receipt filtering can remove ACCEPTED while retaining its DUPLICATE or
    revision after out-of-order capture; the resulting invalid subset fails closed.
    """
    selected, claims, records = _observed_selection(
        sources, policy, cutoff, limits, coverage
    )
    retained: list[SelectedCandle] = []
    for item in selected:
        if policy.revision == "first":
            item = SelectedCandle(item.evidence[0], item.evidence)
        if _retain_source(item.selected, policy, limits, claims, records):
            retained.append(item)
    return tuple(retained), tuple(records)


def _candle_index(
    selected: tuple[SelectedCandle, ...],
) -> dict[CandleKey, SelectedCandle]:
    by_key: dict[CandleKey, SelectedCandle] = {}
    for item in selected:
        semantics = item.selected.value.semantics
        if semantics is None or semantics.key in by_key:
            raise SelectionError("Invalid or duplicate recipe candle")
        by_key[semantics.key] = item
    return by_key


def _derived_chain(
    key: CandleKey,
    by_key: dict[CandleKey, SelectedCandle],
    steps: int,
    direction: int,
    *,
    finalized: bool,
) -> tuple[list[SelectedCandle], str | None]:
    chain: list[SelectedCandle] = []
    for step in range(1, steps + 1):
        other_key = CandleKey(
            instrument=key.instrument,
            interval_ns=key.interval_ns,
            start_ns=key.start_ns + direction * step * key.interval_ns,
        )
        other = by_key.get(other_key)
        if other is None:
            return chain, "gap-or-missing"
        other_semantics = other.selected.value.semantics
        assert other_semantics is not None
        chain.append(other)
        if finalized and not other_semantics.finalized:
            return chain, "not-finalized"
    return chain, None


def _derive_value(
    row: dict[str, Any],
    definition: dict[str, Any],
    key: CandleKey,
    by_key: dict[CandleKey, SelectedCandle],
    name: str,
    steps: int,
    direction: int,
) -> None:
    row[f"{name}_close"] = None
    is_label = name == "label"
    if is_label:
        row["label_end_ns"] = None
    chain, reason = _derived_chain(key, by_key, steps, direction, finalized=is_label)
    definition[f"{name}_source_fingerprints"] = [
        input_fingerprint(other.selected.value) for other in chain
    ]
    definition[f"{name}_missing_reason"] = reason
    if reason is not None:
        return
    target = chain[-1].selected.value.semantics
    assert target is not None
    row[f"{name}_close"] = Decimal(target.close)
    if is_label:
        row["label_end_ns"] = target.key.start_ns + target.key.interval_ns


def derive_rows(
    selected: tuple[SelectedCandle, ...],
    rows: list[dict[str, Any]],
    policy: RecipePolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exact contiguous close lags and finalized future-close labels, without fill."""
    if len(selected) != len(rows):
        raise SelectionError("Recipe rows and selected evidence differ in length")
    by_key = _candle_index(selected)
    result = [dict(row) for row in rows]
    definitions: list[dict[str, Any]] = []
    for index, item in enumerate(selected):
        semantics = item.selected.value.semantics
        assert semantics is not None
        key = semantics.key
        definition: dict[str, Any] = {"row_index": index}
        for name, steps, direction in (
            ("lag", policy.lag_steps, -1),
            ("label", policy.label_horizon_steps, 1),
        ):
            if steps:
                _derive_value(
                    result[index], definition, key, by_key, name, steps, direction
                )
        if policy.lag_steps or policy.label_horizon_steps:
            definitions.append(definition)
    return result, definitions
