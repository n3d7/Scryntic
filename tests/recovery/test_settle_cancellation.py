"""Cancellation must join an in-flight durable operation before propagating."""

import asyncio
import threading

import pytest

from scryntic.recovery.supervisor import _io


def test_repeated_caller_cancellation_waits_for_durable_worker() -> None:
    entered = threading.Event()
    release = threading.Event()
    committed = threading.Event()

    def durable_operation() -> None:
        entered.set()
        if release.wait(timeout=2):
            committed.set()

    async def exercise() -> None:
        owner = asyncio.create_task(_io(durable_operation))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert committed.is_set()

    asyncio.run(exercise())
