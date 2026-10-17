import asyncio
from dataclasses import dataclass

import pytest

from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Operation,
    Request,
)
from scryntic.use_cases import ApplicationServices
from scryntic.workflows.wire import result


@dataclass(frozen=True)
class Status:
    operation = Operation.STATUS


def test_single_admission_and_cancellation_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        service = ApplicationServices()
        started, released = asyncio.Event(), asyncio.Event()

        class Handler:
            async def execute(self, request: Request) -> ApplicationResult:
                started.set()
                await released.wait()
                return result(request.operation, {"settled": True})

            async def cancel(self) -> None:
                released.set()

        handler = Handler()
        monkeypatch.setattr(service, "_handler", lambda request: handler)
        task = asyncio.create_task(service.execute(Status()))
        await started.wait()
        request = Status()
        with pytest.raises(ApplicationError) as busy:
            await service.execute(request)
        assert busy.value.code is ErrorCode.BUSY
        await service.cancel()
        with pytest.raises(ApplicationError) as cancelled:
            await task
        assert cancelled.value.code is ErrorCode.CANCELLED
        assert (await service.execute(Status())).complete
        await service.aclose()

    asyncio.run(scenario())


def test_unexpected_errors_are_fixed_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        service = ApplicationServices()

        class Handler:
            async def execute(self, request: Request) -> ApplicationResult:
                raise ValueError("SENTINEL_SECRET raw payload")

        monkeypatch.setattr(service, "_handler", lambda request: Handler())
        request = Status()
        with pytest.raises(ApplicationError) as error:
            await service.execute(request)
        assert "SENTINEL" not in str(error.value)
        assert error.value.code is ErrorCode.FAILED
        await service.aclose()

    asyncio.run(scenario())
