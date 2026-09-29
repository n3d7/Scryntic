"""Make a bounded, unauthenticated Bybit spot discovery and candle request."""

from __future__ import annotations

import asyncio
import json
import time

from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoryRequest,
    StreamRequest,
)
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.identity import InstrumentId
from scryntic.sources.bybit import BybitHistoricalSource


def main() -> None:
    clock = ClockMonitor(SystemClockReader(), ChronyStatus(), ClockLimits())
    source = BybitHistoricalSource(clock=clock)
    try:
        # F12 intentionally reports unknown on process startup until a later
        # synchronized reference sample confirms the new epoch.
        clock.sample()
        clock.sample()
        instruments = asyncio.run(source.discover("spot"))
        if not any(item.identity.symbol == "BTCUSDT" for item in instruments):
            raise RuntimeError("Bybit spot metadata did not include BTCUSDT")

        end_ns = time.time_ns()
        interval_ns = 60_000_000_000
        page = asyncio.run(
            source.fetch(
                HistoryRequest(
                    StreamRequest(
                        BYBIT_CANDLE_SCHEMA,
                        InstrumentId("bybit", "spot", "BTCUSDT"),
                    ),
                    end_ns - 10 * interval_ns,
                    end_ns,
                    10,
                )
            )
        )
        sample = page.envelopes[0].receipt if page.envelopes else clock.sample()
        print(
            json.dumps(
                {
                    "category": "spot",
                    "symbol": "BTCUSDT",
                    "candles": len(page.envelopes),
                    "first_timestamp_ms": (
                        page.envelopes[0].source_time.value
                        if page.envelopes and page.envelopes[0].source_time
                        else None
                    ),
                    "clock_status": sample.quality.status,
                    "clock_offset_ns": sample.quality.offset_ns,
                    "clock_uncertainty_ns": sample.quality.uncertainty_ns,
                    "clock_evidence_age_ns": sample.quality.evidence_age_ns,
                    "finalized": [
                        json.loads(envelope.payload)["finalized"]
                        for envelope in page.envelopes
                    ],
                    "requests_max": 2,
                },
                sort_keys=True,
            )
        )
        if not page.envelopes:
            raise RuntimeError("Bybit returned no spot candles for the recent window")
    finally:
        asyncio.run(source.close())


if __name__ == "__main__":
    main()
