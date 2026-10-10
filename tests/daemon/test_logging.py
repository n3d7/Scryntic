"""Structured logger never exports arbitrary messages and never waits for a sink."""

import asyncio
import io
import json
import logging
import threading
import time

import pytest

from scryntic.daemon.health import State
from scryntic.daemon.logging import Event, StructuredLogger


class BlockingSink(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, text: str) -> int:
        self.entered.set()
        assert self.release.wait(2)
        return super().write(text)


def test_logger_emits_only_closed_fields_and_suppresses_dependency_messages() -> None:
    sink = io.StringIO()
    logger = StructuredLogger(sink=sink)
    dependency = logging.getLogger("scryntic-test-sensitive-dependency")
    handler = logging.StreamHandler(sink)
    dependency.addHandler(handler)
    dependency.propagate = False
    previous_level = dependency.level
    dependency.setLevel(logging.DEBUG)
    restore = logger.install_standard_logging()
    try:
        dependency.error("SECRET token payload", exc_info=RuntimeError("SECRET"))
        assert logger.emit(Event.STATE, state=State.HEALTHY, count=3)
        with pytest.raises(ValueError):
            logger.emit("SECRET")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            logger.emit(Event.STATE, state="SECRET")  # type: ignore[arg-type]
        assert logger.close(timeout=1)
        body = json.loads(sink.getvalue())
        assert body == {"event": "state", "state": "healthy", "count": 3}
        assert "SECRET" not in sink.getvalue()
    finally:
        restore()
        dependency.removeHandler(handler)
        dependency.setLevel(previous_level)
        dependency.propagate = True


def test_blocked_stderr_drops_overflow_and_close_has_a_deadline() -> None:
    sink = BlockingSink()
    logger = StructuredLogger(sink=sink, capacity=1)
    try:
        assert logger.emit(Event.HEALTH)
        assert sink.entered.wait(1)
        assert logger.emit(Event.HEALTH)
        started = time.monotonic()
        assert not logger.emit(Event.HEALTH)
        assert not logger.close(timeout=0.01)
        assert time.monotonic() - started < 0.2
        assert logger.dropped == 1
        assert not logger.emit(Event.HEALTH)
    finally:
        sink.release.set()
        assert logger.close(timeout=1)
    assert len(sink.getvalue().splitlines()) == 2


def test_loop_exception_context_is_reduced_to_a_fixed_fault() -> None:
    sink = io.StringIO()
    logger = StructuredLogger(sink=sink)
    loop = asyncio.new_event_loop()
    restore = logger.install_asyncio_handler(loop)
    try:
        loop.call_exception_handler(
            {
                "message": "SECRET payload",
                "exception": RuntimeError("SECRET token"),
            }
        )
        assert logger.close(timeout=1)
        assert json.loads(sink.getvalue()) == {"event": "pipeline_fault"}
        assert "SECRET" not in sink.getvalue()
    finally:
        restore()
        loop.close()


def test_sink_failure_does_not_escape_into_service_or_raw_stderr() -> None:
    class FailingSink(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError("SECRET path")

    logger = StructuredLogger(sink=FailingSink())
    assert logger.emit(Event.HEALTH)
    assert logger.close(timeout=1)
    assert logger.dropped == 1
