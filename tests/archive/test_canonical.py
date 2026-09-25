"""Normative canonical publication-input behavior."""

from dataclasses import replace
from hashlib import sha256

import pytest

from scryntic.archive.canonical import (
    PublicationInput,
    canonical_json_bytes,
    input_fingerprint,
    ordered_digest_from_pairs,
    ordered_input_digest,
    raw_projection,
)
from scryntic.domain.identity import EntityId
from scryntic.domain.raw import IngestionId, RawEnvelope, RawRecord
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.normalization.candle import (
    NORMALIZER_VERSION,
    CandleNormalization,
    RejectionCode,
)
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome
from tests.normalization.helpers import normalization, raw_record


_PAYLOAD_SHA256 = "a90a10503fbfc95789ff38a1bb5039cb71869ab9c0eb1cb51c4a9099f2933c6b"
_INPUT_BYTES = (
    b'{"algorithm":"scryntic-publication-input-v1","outcome":{"identity":'
    b'{"epoch":"epoch-a","offset":7,"producer":"prod-a"},"input_schema":null,'
    b'"instrument_revision":null,"instrument_schema":null,"kind":"rejected",'
    b'"normalized_at_ns":null,"normalizer_version":"f06.fake_candle.v1",'
    b'"output_schema":null,"predecessor":null,"raw_sha256":"'
    + _PAYLOAD_SHA256.encode()
    + b'","receipt":{"monotonic_ns":90,"quality":{"epoch":"quality-a",'
    b'"evidence_age_ns":null,"offset_ns":null,"status":"unknown",'
    b'"uncertainty_ns":null},"session_id":"session-a","wall_time_ns":100},'
    b'"rejection_code":"invalid_utf8","rejection_field":null,'
    b'"semantic_revision":null},"raw":{"adapter_version":"v1",'
    b'"channel":"public","content_sha256":"'
    + _PAYLOAD_SHA256.encode()
    + b'","identity":{"epoch":"epoch-a","offset":7,"producer":"prod-a"},'
    b'"payload_hex":"00ff41","receipt":{"monotonic_ns":90,"quality":'
    b'{"epoch":"quality-a","evidence_age_ns":null,"offset_ns":null,'
    b'"status":"unknown","uncertainty_ns":null},"session_id":"session-a",'
    b'"wall_time_ns":100},"source":"fake","source_event_id":null,'
    b'"source_sequence":null,"source_time":null,"stream":"candles",'
    b'"subject":null},"semantics":null}'
)


def _rejected_vector() -> PublicationInput:
    receipt = ClockSample(100, 90, "session-a", TimeQuality("quality-a"))
    envelope = RawEnvelope(
        source="fake",
        stream="candles",
        channel="public",
        adapter_version="v1",
        receipt=receipt,
        payload=b"\x00\xffA",
        payload_limit=3,
    )
    assert envelope.content_sha256 == _PAYLOAD_SHA256
    identity = IngestionId("prod-a", "epoch-a", 7)
    raw = RawRecord(identity, envelope)
    outcome = ProcessingOutcome(
        identity=identity,
        predecessor=None,
        raw_sha256=_PAYLOAD_SHA256,
        kind=OutcomeKind.REJECTED,
        semantic_revision=None,
        rejection_code=RejectionCode.INVALID_UTF8,
        rejection_field=None,
        input_schema=None,
        instrument_schema=None,
        output_schema=None,
        instrument_revision=None,
        normalizer_version=NORMALIZER_VERSION,
        receipt=receipt,
        normalized_at_ns=None,
    )
    return PublicationInput(raw, outcome, None)


def _accepted(*, offset: int = 1, epoch: str = "epoch-a") -> PublicationInput:
    raw = raw_record(offset=offset, epoch=epoch)
    result = normalization(raw)
    assert isinstance(result, CandleNormalization)
    outcome = ProcessingOutcome(
        identity=raw.identity,
        predecessor=None,
        raw_sha256=raw.envelope.content_sha256,
        kind=OutcomeKind.ACCEPTED,
        semantic_revision=result.semantics.revision(),
        rejection_code=None,
        rejection_field=None,
        input_schema=result.input_schema,
        instrument_schema=result.instrument_schema,
        output_schema=result.candle.schema,
        instrument_revision=result.instrument_revision,
        normalizer_version=result.candle.normalizer_version,
        receipt=raw.envelope.receipt,
        normalized_at_ns=result.candle.normalized_at_ns,
    )
    return PublicationInput(raw, outcome, result.semantics)


def test_publication_input_vector_matches_spec_bytes_and_hash() -> None:
    value = _rejected_vector()

    assert value.canonical_bytes() == _INPUT_BYTES
    assert input_fingerprint(value) == (
        "aab4bbe154acd69d3bf0b535de0dd15741a4f870a8c5cb7d40246745ca7f7838"
    )


def test_ordered_input_vector_matches_spec() -> None:
    pairs = (
        (
            IngestionId("prod-a", "epoch-a", 7),
            "aab4bbe154acd69d3bf0b535de0dd15741a4f870a8c5cb7d40246745ca7f7838",
        ),
        (
            IngestionId("prod-a", "epoch-b", 11),
            "0000000000000000000000000000000000000000000000000000000000000001",
        ),
    )

    assert ordered_digest_from_pairs(pairs) == (
        "fa95b24754be337ca3f78d08123abfbee75d4b92f81c05f7456db592a1f1e842"
    )


def test_binary_payload_projection_is_lowercase_even_hex_and_empty_is_empty() -> None:
    binary = _rejected_vector()
    empty_envelope = replace(
        binary.raw.envelope,
        payload=b"",
        payload_limit=1,
    )
    empty = PublicationInput(
        RawRecord(binary.raw.identity, empty_envelope),
        replace(
            binary.outcome,
            raw_sha256=sha256(b"").hexdigest(),
        ),
        None,
    )

    assert raw_projection(binary.raw)["payload_hex"] == "00ff41"
    assert raw_projection(empty.raw)["payload_hex"] == ""


def test_subject_projection_distinguishes_entity_from_instrument_and_null() -> None:
    accepted = _accepted()
    entity_envelope = replace(
        accepted.raw.envelope,
        subject=EntityId("document", "publisher", "story-7"),
        payload_limit=8192,
    )
    entity_raw = RawRecord(accepted.raw.identity, entity_envelope)

    assert raw_projection(accepted.raw)["subject"] == {
        "type": "instrument",
        "venue": "fake-venue",
        "category": "spot",
        "symbol": "BTC-USDT",
    }
    assert raw_projection(entity_raw)["subject"] == {
        "type": "entity",
        "kind": "document",
        "namespace": "publisher",
        "value": "story-7",
    }
    assert raw_projection(_rejected_vector().raw)["subject"] is None


@pytest.mark.parametrize(
    "mutator, message",
    [
        (
            lambda value: replace(
                value,
                outcome=replace(
                    value.outcome,
                    identity=IngestionId("prod-a", "epoch-a", 8),
                ),
            ),
            "identity",
        ),
        (
            lambda value: replace(
                value,
                outcome=replace(value.outcome, raw_sha256="0" * 64),
            ),
            "hash",
        ),
        (
            lambda value: replace(
                value,
                outcome=replace(
                    value.outcome,
                    receipt=ClockSample(
                        101, 90, "session-a", TimeQuality("quality-a")
                    ),
                ),
            ),
            "receipt",
        ),
    ],
)
def test_input_rejects_raw_outcome_mismatches(mutator: object, message: str) -> None:
    value = _rejected_vector()

    with pytest.raises(ValueError, match=message):
        mutator(value)  # type: ignore[operator]


def test_input_requires_semantics_exactly_for_non_rejected_outcomes() -> None:
    accepted = _accepted()

    with pytest.raises(ValueError, match="semantics"):
        PublicationInput(accepted.raw, accepted.outcome, None)
    with pytest.raises(ValueError, match="semantics"):
        PublicationInput(
            _rejected_vector().raw,
            _rejected_vector().outcome,
            accepted.semantics,
        )


def test_ordered_digest_requires_strictly_increasing_offsets() -> None:
    values = (_accepted(offset=5), _accepted(offset=2))

    with pytest.raises(ValueError, match="order"):
        ordered_input_digest(values)


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1, 1.5, {1: "bad"}])
def test_canonical_json_rejects_values_outside_v1(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes(value)  # type: ignore[arg-type]
