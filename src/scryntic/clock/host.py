"""Linux host samples using read-only wall, monotonic and suspend-aware clocks."""

import time
from uuid import uuid4

from scryntic.application.clock import ClockReading


class SystemClockReader:
    def __init__(self) -> None:
        # A new process/reader session never reuses monotonic ordering or epochs.
        self._session_id = uuid4().hex

    def read(self) -> ClockReading:
        start = time.monotonic_ns()
        wall = time.time_ns()
        boot: int | None = None
        if hasattr(time, "CLOCK_BOOTTIME"):
            try:
                boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            except OSError:
                pass
        end = time.monotonic_ns()
        return ClockReading(
            wall, (start + end) // 2, boot, self._session_id, abs(end - start)
        )
