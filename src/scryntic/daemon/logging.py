"""Fixed-field stderr events with a bounded nonblocking producer boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import sys
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import TextIO

from scryntic.daemon.health import MAX_INTEGER, Fault, State


class Event(StrEnum):
    STATE = "state"
    SOURCE_FAULT = "source_fault"
    PIPELINE_FAULT = "pipeline_fault"
    SHUTDOWN = "shutdown"
    HEALTH = "health"


class StructuredLogger:
    """The daemon writer may block; intake only enqueues bounded public events."""

    def __init__(self, *, sink: TextIO | None = None, capacity: int = 128) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("log queue capacity is outside its bounded range")
        self._sink = sys.stderr if sink is None else sink
        self._queue: queue.Queue[str] = queue.Queue(maxsize=capacity)
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._dropped = 0
        self._writer = threading.Thread(
            target=self._write, name="daemon-stderr", daemon=True
        )
        self._writer.start()

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def emit(
        self,
        event: Event,
        *,
        state: State | None = None,
        fault: Fault | None = None,
        stream_ordinal: int | None = None,
        count: int | None = None,
    ) -> bool:
        if type(event) is not Event:
            raise ValueError("log event must use a closed enum")
        if state is not None and type(state) is not State:
            raise ValueError("log state must use a closed enum")
        if fault is not None and type(fault) is not Fault:
            raise ValueError("log fault must use a closed enum")
        fields: dict[str, str | int] = {"event": event.value}
        if state is not None:
            fields["state"] = state.value
        if fault is not None:
            fields["fault"] = fault.value
        for name, value in (("stream_ordinal", stream_ordinal), ("count", count)):
            if value is not None:
                if type(value) is not int or not 0 <= value <= MAX_INTEGER:
                    raise ValueError("log integer is outside its bounded range")
                fields[name] = value
        line = json.dumps(fields, separators=(",", ":")) + "\n"
        return self._enqueue(line)

    def _enqueue(self, line: str) -> bool:
        with self._lock:
            if self._closing.is_set():
                return False
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                self._dropped = min(MAX_INTEGER, self._dropped + 1)
                return False
        return True

    def close(self, *, timeout: float = 0.1) -> bool:
        """Stop admission and wait at most timeout; never cancel an active write."""
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("logger close timeout must be finite and nonnegative")
        with self._lock:
            self._closing.set()
        self._writer.join(timeout)
        return not self._writer.is_alive()

    def install_standard_logging(self) -> Callable[[], None]:
        """Suppress arbitrary dependency messages, including named local handlers."""
        previous = logging.root.manager.disable
        logging.disable(sys.maxsize)

        def restore() -> None:
            logging.disable(previous)

        return restore

    def install_asyncio_handler(
        self, loop: asyncio.AbstractEventLoop
    ) -> Callable[[], None]:
        """Drop exception text and task representations from the default handler."""
        previous = loop.get_exception_handler()

        def handler(
            active_loop: asyncio.AbstractEventLoop, context: dict[str, object]
        ) -> None:
            self.emit(Event.PIPELINE_FAULT)

        loop.set_exception_handler(handler)

        def restore() -> None:
            loop.set_exception_handler(previous)

        return restore

    def _write(self) -> None:
        while not self._closing.is_set() or not self._queue.empty():
            try:
                line = self._queue.get(timeout=0.01)
            except queue.Empty:
                continue
            try:
                self._sink.write(line)
                self._sink.flush()
            except Exception:
                # Sink diagnostics may contain paths or secrets; never report them.
                with self._lock:
                    self._dropped = min(MAX_INTEGER, self._dropped + 1)
            finally:
                self._queue.task_done()
