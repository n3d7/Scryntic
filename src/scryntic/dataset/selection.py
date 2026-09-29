"""Deterministic selection of validated publication inputs."""

from dataclasses import dataclass

from scryntic.archive.canonical import PublicationInput
from scryntic.clock.policy import observed_by
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.market import CandleKey
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import digest, integer
from scryntic.normalization.sqlite_store import OutcomeKind


class SelectionError(RuntimeError):
    """A selected input cannot form an unambiguous logical snapshot."""


@dataclass(frozen=True, slots=True)
class SourceInput:
    manifest_sha256: str
    raw_object_sha256: str
    normalized_object_sha256: str
    ordinal: int
    value: PublicationInput

    def __post_init__(self) -> None:
        for value in (
            self.manifest_sha256,
            self.raw_object_sha256,
            self.normalized_object_sha256,
        ):
            digest(value)
        integer(self.ordinal, 0)
        if not isinstance(self.value, PublicationInput):
            raise TypeError("Expected validated publication input")


@dataclass(frozen=True, slots=True)
class SelectedCandle:
    selected: SourceInput
    evidence: tuple[SourceInput, ...]


@dataclass(slots=True)
class _KeyState:
    selected: SourceInput
    evidence: list[SourceInput]
    revisions: set[str]


def strict_eligible(
    inputs: tuple[SourceInput, ...], cutoff: ClockSample, limits: ClockLimits
) -> tuple[tuple[SourceInput, ...], tuple[dict[str, object], ...]]:
    """Filter uncertain receipts without hiding known invalid publication inputs."""
    eligible: list[SourceInput] = []
    exclusions: list[dict[str, object]] = []
    for source in inputs:
        if source.value.outcome.kind in (OutcomeKind.CONFLICT, OutcomeKind.REJECTED):
            raise SelectionError("Conflicting or rejected dataset input")
        if not observed_by(source.value.raw.envelope.receipt, cutoff, limits):
            identity = source.value.raw.identity
            exclusions.append(
                {
                    "producer": identity.producer,
                    "epoch": identity.epoch,
                    "offset": identity.offset,
                    "reason": "not_proven_before_cutoff",
                }
            )
            continue
        eligible.append(source)
    return tuple(eligible), tuple(exclusions)


def select_candles(inputs: tuple[SourceInput, ...]) -> tuple[SelectedCandle, ...]:
    """Select the latest non-conflicting revision per key in ingestion order."""
    if not inputs:
        raise SelectionError("Dataset selection is empty")
    ordered = sorted(
        inputs,
        key=lambda item: (
            item.value.raw.identity.producer,
            item.value.raw.identity.offset,
            item.value.raw.identity.epoch,
        ),
    )
    producer = ordered[0].value.raw.identity.producer
    seen: set[tuple[str, int]] = set()
    states: dict[CandleKey, _KeyState] = {}
    for source in ordered:
        value = source.value
        identity = value.raw.identity
        ingestion_key = (identity.producer, identity.offset)
        if identity.producer != producer or ingestion_key in seen:
            raise SelectionError("Overlapping or mixed-producer dataset inputs")
        seen.add(ingestion_key)
        kind = value.outcome.kind
        semantics = value.semantics
        if kind in (OutcomeKind.CONFLICT, OutcomeKind.REJECTED):
            raise SelectionError("Conflicting or rejected dataset input")
        if semantics is None or value.outcome.semantic_revision is None:
            raise SelectionError("Missing dataset candle semantics")
        revision = value.outcome.semantic_revision
        state = states.get(semantics.key)
        if kind is OutcomeKind.ACCEPTED:
            if state is not None:
                raise SelectionError("Repeated accepted logical candle")
            states[semantics.key] = _KeyState(source, [source], {revision})
            continue
        if state is None:
            raise SelectionError("Dataset revision has no accepted predecessor")
        if kind is OutcomeKind.DUPLICATE:
            if revision not in state.revisions:
                raise SelectionError("Duplicate lacks a matching revision")
            state.evidence.append(source)
            continue
        previous = state.selected.value.semantics
        if (
            previous is None
            or previous.finalized
            or revision in state.revisions
            or (kind is OutcomeKind.OPEN_REVISION and semantics.finalized)
            or (kind is OutcomeKind.FINALIZATION and not semantics.finalized)
            or kind not in (OutcomeKind.OPEN_REVISION, OutcomeKind.FINALIZATION)
        ):
            raise SelectionError("Invalid dataset revision transition")
        state.selected = source
        state.evidence.append(source)
        state.revisions.add(revision)
    return tuple(
        SelectedCandle(state.selected, tuple(state.evidence))
        for key, state in sorted(
            states.items(),
            key=lambda pair: (
                pair[0].start_ns,
                pair[0].instrument.venue,
                pair[0].instrument.category,
                pair[0].instrument.symbol,
                pair[0].interval_ns,
            ),
        )
    )
