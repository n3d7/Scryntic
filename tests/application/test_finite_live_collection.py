"""Finite live collection distinguishes intentional close from source failure."""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.application.local_operations import CollectRequest
from scryntic.application.sources import (
    SourceConfigurationError,
    SourceLoss,
    SourceTransientError,
    StreamRequest,
)
from scryntic.clock.monitor import ClockMonitor
from scryntic.daemon.components import Services
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.health import Fault
from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import RawEnvelope
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.fake import fake_instrument
from scryntic.workflows.collection_host import _Finite, collection_result
from tests.application.test_local_operations import FreshSynchronizationStatus
from tests.daemon.test_config import installation
from tests.sources.test_bybit_live import candle


class RecordedLiveSource(BybitLiveSource):
    """A recorded live session with observable transport ownership."""

    def __init__(self, *, clock: ClockMonitor) -> None:
        super().__init__(clock=clock)
        self.fail = asyncio.Event()

    async def stream(self, request: StreamRequest) -> AsyncGenerator[RawEnvelope, None]:
        self.connected = True
        self.last_heartbeat_monotonic_ns = time.monotonic_ns()
        start_ms = time.time_ns() // 60_000_000_000 * 60_000
        data = candle()
        data.update(start=start_ms, end=start_ms + 59_999)
        assert isinstance(request.subject, InstrumentId)
        yield self._envelope(data, {"ts": start_ms + 30_000}, request.subject)[0]
        await self.fail.wait()
        raise SourceConfigurationError("Recorded source failed")

    async def close(self) -> None:
        self.connected = False
        await super().close()


class RecordedHistory(BybitHistoricalSource):
    async def fetch(self, request):  # type: ignore[no-untyped-def]
        raise SourceTransientError("Recorded history unavailable")


@pytest.mark.parametrize(
    "failure", (None, "stale", "disconnect", "source", "late-loss", "late-source")
)
def test_live_collection_completion_preserves_source_failures(
    tmp_path: Path, failure: str | None
) -> None:
    async def run() -> None:
        request = CollectRequest(profile="bybit", seconds=1)
        metadata = replace(
            fake_instrument(), identity=InstrumentId("bybit", "spot", "BTCUSDT")
        )
        services = Services(
            DaemonConfig(installation(tmp_path), pipeline_interval_s=0.01),
            synchronization_status=FreshSynchronizationStatus(),
            instruments=(metadata,),
            source_factory=lambda clock: RecordedLiveSource(clock=clock),
            history_factory=lambda clock: RecordedHistory(clock=clock),
        )
        finite = _Finite(services, request)
        await services.start()
        try:
            async with asyncio.timeout(5):
                while services.health().published != 1:
                    await asyncio.sleep(0.01)
            source = services._source
            assert isinstance(source, RecordedLiveSource)
            assert Fault.SOURCE not in services.health().faults
            if failure == "stale":
                services.config = replace(services.config, freshness_limit_ns=1)
            elif failure == "disconnect":
                source.connected = False
            elif failure == "source":
                source.fail.set()
                async with asyncio.timeout(5):
                    while not services.source_finished:
                        await asyncio.sleep(0.01)
            services._update_health()
            assert (Fault.SOURCE in services.health().faults) == (
                failure in ("stale", "disconnect", "source")
            )
            finite.stop_intake()
            if failure == "late-loss":
                assert services._supervisor is not None
                await services._supervisor.transport_loss(
                    SourceLoss("disconnect", services._clock.sample())
                )
            elif failure == "late-source":
                source.fail.set()
                async with asyncio.timeout(5):
                    while not services.source_finished:
                        await asyncio.sleep(0.01)
            await finite.drain()
            await finite.close()
            health = services.health()
            assert not health.live
            assert not health.streams[0].connected
            assert Fault.SOURCE in health.faults
            completed = collection_result(services, request)
            fields = json.loads(completed.payload)
            assert completed.complete is (failure is None), fields
            assert fields["shutdown_complete"]
            assert fields["received"] == fields["durably_committed"] == 1
            assert fields["normalized"] == fields["published"] == 1
            if failure is None:
                assert fields["faults"] == []
        finally:
            if not services._closed:
                services.stop_intake()
                await services.drain()
                await services.close()

    asyncio.run(run())
