"""Unprivileged clock evidence port; health policy is owned by the application."""

from typing import Protocol

from scryntic.domain.time import ClockSample


class Clock(Protocol):
    def sample(self) -> ClockSample: ...
