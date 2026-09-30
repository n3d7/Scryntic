"""Provider decoding bridge; recovery policy does not parse Bybit transport JSON."""

import json
from hashlib import sha256

from scryntic.domain.raw import RawEnvelope
from scryntic.normalization.bybit_candle import inspect_bybit_envelope
from scryntic.normalization.candle import canonical_decimal
from scryntic.recovery.coverage import CandleEvidence


def candle_evidence(envelope: RawEnvelope) -> CandleEvidence | None:
    result = inspect_bybit_envelope(envelope)
    if result is None:
        return None
    values = [
        result.start_ns,
        result.interval_ns,
        *(
            canonical_decimal(value)
            for value in (
                result.open,
                result.high,
                result.low,
                result.close,
                result.volume,
            )
        ),
    ]
    fingerprint = sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
    return CandleEvidence(
        result.start_ns, result.interval_ns, result.finalized, fingerprint
    )
