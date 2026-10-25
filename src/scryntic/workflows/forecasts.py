"""Typed presentation over the same owner-only PI04 services."""

import asyncio
import threading

from scryntic.application.forecast_operations import (
    ForecastCompareRequest,
    ForecastInspectRequest,
    ForecastMatureRequest,
    ForecastRunRequest,
    ForecastStatusRequest,
)
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
)
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.configuration.clock import ClockLimits
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.forecast.forward_service import ForwardSession
from scryntic.forecast.inference import InferenceStore
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import ForecastContent, ForwardAdmission
from scryntic.forecast.scoring import ForecastScores
from scryntic.research.targets import ISSUE_DELAY_NS
from scryntic.workflows.context import WorkflowContext
from scryntic.workflows.wire import result


class _ReadClock:
    def __init__(self) -> None:
        self.reader = SystemClockReader()

    def sample(self) -> ClockSample:
        reading = self.reader.read()
        return ClockSample(
            reading.wall_time_ns,
            reading.monotonic_ns,
            reading.session_id,
            TimeQuality("pi04-unqualified-read-clock"),
        )


class ForecastWorkflows:
    def __init__(self, context: WorkflowContext) -> None:
        self.context = context
        self._cancel_pending = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[ApplicationResult] | None = None

    async def execute(self, request: Request) -> ApplicationResult:
        if isinstance(request, (ForecastRunRequest, ForecastMatureRequest)):
            self._cancel_pending.clear()
            return await self.context.run_io(lambda: self._operate(request))
        if not isinstance(
            request,
            (ForecastStatusRequest, ForecastInspectRequest, ForecastCompareRequest),
        ):
            raise ApplicationError(ErrorCode.INVALID)
        return await self.context.run_io(lambda: self._read(request))

    def _operate(
        self, request: ForecastRunRequest | ForecastMatureRequest
    ) -> ApplicationResult:
        clock = ClockMonitor(SystemClockReader(), ChronyStatus(), ClockLimits())
        with (
            ForecastLedger(
                self.context.installation, clock, admission=ForwardAdmission()
            ) as ledger,
            InferenceStore(
                self.context.installation,
                ledger,
                clock=lambda: clock.sample().wall_time_ns,
            ) as store,
        ):
            session = ForwardSession(store, ledger)

            async def run() -> ApplicationResult:
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                try:
                    if self._cancel_pending.is_set():
                        raise asyncio.CancelledError
                    data = (
                        await session.run(request.wait_seconds)
                        if isinstance(request, ForecastRunRequest)
                        else await session.mature(request.limit)
                    )
                    return result(request.operation, data)
                finally:
                    await session.inference.aclose()
                    self._loop, self._task = None, None

            return asyncio.run(run())

    async def cancel(self) -> None:
        self._cancel_pending.set()
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)

    def _read(
        self,
        request: ForecastStatusRequest
        | ForecastInspectRequest
        | ForecastCompareRequest,
    ) -> ApplicationResult:
        clock = _ReadClock()
        with ForecastLedger(self.context.installation, clock) as ledger:
            scores = ForecastScores(ledger)
            now = clock.sample().wall_time_ns
            if isinstance(request, ForecastInspectRequest):
                body = ledger.publication(request.slot_sha256)
                content = ForecastContent.from_body(body["content"])
                as_of = request.as_of_ns or now
                return result(
                    request.operation,
                    {
                        "publication": body,
                        "receipt": ledger.receipt(request.slot_sha256),
                        "expected_maturity_ns": content.slot.endpoint_ns
                        + ISSUE_DELAY_NS,
                        "first_score": scores.view(
                            request.slot_sha256, as_of, first_available=True
                        ),
                        "revised_score": scores.view(request.slot_sha256, as_of),
                    },
                )
            if isinstance(request, ForecastCompareRequest):
                return result(
                    request.operation,
                    scores.compare(
                        request.left,
                        request.right,
                        request.as_of_ns or now,
                        first_available=request.view == "first-available",
                    ),
                )
            events = scores.cohort(now)
            entries = []
            for event in events[request.offset : request.offset + request.limit]:
                slot = event["slot"]
                receipt = event["receipt"]
                entries.append(
                    {
                        "slot_sha256": event["slot_sha256"],
                        "stream": slot["stream"]["stream_id"],
                        "instrument": slot["instrument"],
                        "horizon_seconds": slot["horizon_seconds"],
                        "evidence_class": receipt["evidence_class"]
                        if receipt
                        else slot["stream"]["evidence_class"],
                        "timeliness": receipt["timeliness"] if receipt else None,
                        "deadline_ns": slot["origin_ns"] + ISSUE_DELAY_NS,
                        "state": event["state"],
                        "reason": event["reason"],
                        "expected_maturity_ns": slot["origin_ns"]
                        + slot["horizon_seconds"] * 10**9
                        + ISSUE_DELAY_NS,
                    }
                )
            return result(
                request.operation,
                {
                    **ledger.status(),
                    "entries": entries,
                    "limit": request.limit,
                    "offset": request.offset,
                    "total": len(events),
                },
            )
