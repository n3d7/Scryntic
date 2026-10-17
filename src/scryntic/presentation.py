"""Foreground diagnostics use the existing F23 fixed-field log boundary."""

from collections.abc import Iterator
from contextlib import contextmanager

from scryntic.daemon.logging import StructuredLogger


@contextmanager
def diagnostics_boundary() -> Iterator[StructuredLogger]:
    logger = StructuredLogger()
    restore = logger.install_standard_logging()
    try:
        yield logger
    finally:
        restore()
        logger.close(timeout=0.1)
