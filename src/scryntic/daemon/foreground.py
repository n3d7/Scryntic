"""Foreground process host with an explicit process-exit deadline.

No worker cancellation is reported as termination. The final fallback exits this
process with failure; existing journals and isolation controls own restart safety.
"""

import asyncio
import os
import signal
import threading
from collections.abc import Callable
from types import FrameType

from scryntic.daemon.health import Fault, State
from scryntic.daemon.logging import Event, StructuredLogger
from scryntic.daemon.runtime import Daemon, LifecycleLimits, Services


def run_foreground(
    services: Services,
    *,
    limits: LifecycleLimits | None = None,
    logger: StructuredLogger | None = None,
) -> int:
    limits = limits or LifecycleLimits()
    logger = logger or StructuredLogger()
    loop = asyncio.new_event_loop()
    daemon = Daemon(services, limits=limits, logger=logger)
    timers: list[threading.Timer] = []
    stopped = threading.Event()
    uninstall_logging = logger.install_standard_logging()
    uninstall_asyncio = logger.install_asyncio_handler(loop)
    previous: dict[int, Callable[[int, FrameType | None], object] | int | None] = {}

    def hard_exit() -> None:
        if not stopped.is_set():
            logger.emit(Event.SHUTDOWN, state=State.FAILED, fault=Fault.SHUTDOWN)
            logger.close(timeout=0.05)
            os._exit(1)

    def received(signum: int, frame: FrameType | None) -> None:
        # Python signal handlers execute in the main thread even when an event
        # loop callback is stalled. The watchdog bounds interpreter/executor exit.
        if not timers:
            timer = threading.Timer(limits.shutdown_s, hard_exit)
            timer.daemon = True
            timers.append(timer)
            timer.start()
        loop.call_soon_threadsafe(daemon.request_stop)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, received)
        outcome = loop.run_until_complete(daemon.run())
        if not outcome.complete:
            hard_exit()
        loop.run_until_complete(loop.shutdown_asyncgens())
        logger.close(timeout=0.05)
        stopped.set()
        return outcome.exit_code
    except (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        # The caller never receives untrusted exception formatting on stderr.
        logger.emit(Event.SHUTDOWN, state=State.FAILED, fault=Fault.SHUTDOWN)
        hard_exit()
        raise SystemExit(1) from None
    finally:
        stopped.set()
        for timer in timers:
            timer.cancel()
        for restored_signum, handler in previous.items():
            signal.signal(restored_signum, handler)
        uninstall_asyncio()
        uninstall_logging()
        loop.close()
