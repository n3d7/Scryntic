"""Trusted in-process fixtures only; no runtime integration or persistence."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from scryntic.application.clock import Clock
from scryntic.application.providers import (
    ForecastPoint,
    ForecastProvider,
    ForecastRequest,
    ForecastResult,
)
from scryntic.application.sources import SourceOperation, StreamingSource, StreamRequest
from scryntic.domain.identity import InstrumentId
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample
from tests.contracts.test_domain import envelope, sample
from tests.contracts.test_ports import descriptor, source_descriptor


@dataclass
class FakeClock:
    current: ClockSample = sample()

    def sample(self) -> ClockSample:
        return self.current


class FakeSource:
    descriptor = source_descriptor()

    def __init__(self) -> None:
        self.closed = False

    async def stream(self, request: StreamRequest) -> AsyncIterator[RawEnvelope]:
        self.descriptor.require(request.schema, SourceOperation.STREAM, request.subject)
        if self.closed:
            raise ValueError("Source closed")
        yield envelope()

    async def close(self) -> None:
        self.closed = True


class FakeProvider:
    descriptor = descriptor()

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        self.descriptor.require(request)
        result = ForecastResult(
            request.request_id,
            request.dataset,
            self.descriptor.provider_id,
            self.descriptor.model.revision,
            tuple(
                ForecastPoint((i + 1) * request.frequency_ns, float(i + 1))
                for i in range(request.horizon)
            ),
        )
        result.validate_for(request, self.descriptor)
        return result


async def exercise(
    source: StreamingSource,
    clock: Clock,
    provider: ForecastProvider,
    request: ForecastRequest,
) -> tuple[bytes, tuple[float, ...]]:
    observed = clock.sample()
    stream = StreamRequest(
        request.dataset.schema, InstrumentId("fake", "spot", "BTC-USDT")
    )
    payloads = [event.payload async for event in source.stream(stream)]
    await source.close()
    result = await provider.forecast(request)
    result.validate_for(request, provider.descriptor)
    assert observed.session_id == "boot-a"
    return b"".join(payloads), tuple(point.value for point in result.points)
