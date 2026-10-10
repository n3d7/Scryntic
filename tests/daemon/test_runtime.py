import asyncio
import io
from dataclasses import replace

import pytest

from scryntic.daemon.health import HealthSnapshot, State
from scryntic.daemon.logging import StructuredLogger
from scryntic.daemon.runtime import Daemon, LifecycleLimits


class Services:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.opened = asyncio.Event()
        self.release = asyncio.Event()
        self.stall = False
        self.fail_start = False
        self.snapshot = HealthSnapshot()

    async def start(self) -> None:
        self.steps.append("start")
        if self.fail_start:
            raise RuntimeError("SENTINEL-secret-raw-payload")
        self.snapshot = replace(self.snapshot, state=State.HEALTHY)
        self.opened.set()

    def stop_intake(self) -> None:
        self.steps.append("stop_intake")

    async def drain(self) -> None:
        self.steps.append("drain")
        if self.stall:
            await self.release.wait()

    async def close(self) -> None:
        self.steps.append("close")

    def health(self) -> HealthSnapshot:
        return self.snapshot


def test_stop_intake_precedes_drain_and_close() -> None:
    async def exercise() -> None:
        services = Services()
        daemon = Daemon(services)
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        daemon.request_stop()
        outcome = await task
        assert outcome.exit_code == 0
        assert outcome.complete
        assert services.steps == ["start", "stop_intake", "drain", "close"]
        assert daemon.health().state is State.STOPPING
        assert not daemon.health().ready

    asyncio.run(exercise())


def test_failed_durable_drain_remains_incomplete_after_close() -> None:
    class FailedDrain(Services):
        async def drain(self) -> None:
            self.steps.append("drain")
            raise RuntimeError("SENTINEL-undrained-secret")

    async def exercise() -> None:
        services = FailedDrain()
        daemon = Daemon(services)
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        daemon.request_stop()
        outcome = await task
        assert outcome.exit_code == 1
        assert not outcome.complete
        assert services.steps[-2:] == ["drain", "close"]
        assert daemon.health().shutdown_phase.value == "incomplete"

    asyncio.run(exercise())


def test_stalled_drain_is_bounded_and_remains_owned() -> None:
    async def exercise() -> None:
        services = Services()
        services.stall = True
        daemon = Daemon(services, limits=LifecycleLimits(shutdown_s=0.02))
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        daemon.request_stop()
        outcome = await asyncio.wait_for(task, 0.5)
        assert not outcome.complete
        assert outcome.exit_code == 1
        assert daemon.unfinished_operations == 1
        assert "close" not in services.steps
        assert daemon.health().state is State.FAILED
        assert not daemon.health().ready
        services.release.set()
        await asyncio.gather(*daemon.operations)
        assert daemon.unfinished_operations == 0

    asyncio.run(exercise())


def test_repeated_stop_does_not_repeat_intake_or_close() -> None:
    async def exercise() -> None:
        services = Services()
        services.stall = True
        daemon = Daemon(services, limits=LifecycleLimits(shutdown_s=30))
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        daemon.request_stop()
        await asyncio.sleep(0)
        daemon.request_stop()
        daemon.request_stop()
        outcome = await asyncio.wait_for(task, 0.5)
        assert not outcome.complete
        assert services.steps.count("stop_intake") == 1
        assert "close" not in services.steps
        services.release.set()
        await asyncio.gather(*daemon.operations)

    asyncio.run(exercise())


def test_startup_failure_is_not_ready_and_logs_no_exception() -> None:
    async def exercise() -> None:
        services = Services()
        services.fail_start = True
        sink = io.StringIO()
        logger = StructuredLogger(sink=sink)
        daemon = Daemon(services, logger=logger)
        outcome = await daemon.run()
        assert outcome.exit_code == 1
        assert daemon.health().state is State.FAILED
        assert not daemon.health().ready
        assert services.steps[-1] == "close"
        assert logger.close(timeout=1)
        assert "SENTINEL" not in sink.getvalue()
        assert "Traceback" not in sink.getvalue()

    asyncio.run(exercise())


def test_failed_close_is_not_a_completed_shutdown() -> None:
    class FailedClose(Services):
        async def close(self) -> None:
            raise RuntimeError("SENTINEL-close-secret")

    async def exercise() -> None:
        services = FailedClose()
        daemon = Daemon(services)
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        daemon.request_stop()
        outcome = await task
        assert not outcome.complete
        assert outcome.exit_code == 1

    asyncio.run(exercise())


def test_failed_storage_health_stops_intake_and_exits() -> None:
    async def exercise() -> None:
        services = Services()
        daemon = Daemon(services, limits=LifecycleLimits(poll_s=0.001))
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        services.snapshot = replace(services.snapshot, state=State.FAILED)
        outcome = await asyncio.wait_for(task, 0.5)
        assert outcome.exit_code == 1
        assert services.steps[-3:] == ["stop_intake", "drain", "close"]

    asyncio.run(exercise())


def test_caller_cancellation_completes_shutdown_before_propagation() -> None:
    async def exercise() -> None:
        services = Services()
        daemon = Daemon(services)
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation was swallowed")
        assert daemon.outcome is not None
        assert daemon.outcome.complete
        assert services.steps[-2:] == ["drain", "close"]

    asyncio.run(exercise())


def test_repeated_cancellation_forces_bounded_shutdown_without_losing_owner() -> None:
    async def exercise() -> None:
        services = Services()
        services.stall = True
        daemon = Daemon(services, limits=LifecycleLimits(shutdown_s=30, poll_s=0.01))
        task = asyncio.create_task(daemon.run())
        await services.opened.wait()
        task.cancel()
        async with asyncio.timeout(1):
            while "drain" not in services.steps:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert daemon.outcome is not None
        assert not daemon.outcome.complete
        assert daemon.unfinished_operations == 1
        assert services.steps.count("stop_intake") == 1
        assert "close" not in services.steps
        services.release.set()
        await asyncio.gather(*daemon.operations)
        assert daemon.unfinished_operations == 0

    asyncio.run(exercise())
