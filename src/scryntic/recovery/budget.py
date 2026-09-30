"""Event-loop-owned admission shared by live intake and historical repair."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from scryntic.domain.validation import integer


@dataclass(slots=True)
class Reservation:
    budget: SharedBudget
    records: int
    bytes_: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            raise RuntimeError("Reservation already released")
        self.released = True
        self.budget._records -= self.records
        self.budget._bytes -= self.bytes_


class SharedBudget:
    """Reserve a whole page before fetch; never lend live headroom to backfill."""

    def __init__(
        self,
        *,
        records: int = 32,
        bytes_: int = 262_144,
        live_records: int = 8,
        live_bytes: int = 65_536,
        request_interval_s: float = 1.0,
    ) -> None:
        for value in (records, bytes_, live_records, live_bytes):
            integer(value, 1)
        if (
            live_records >= records
            or live_bytes >= bytes_
            or not 0 <= request_interval_s <= 60
        ):
            raise ValueError("Invalid shared source budget")
        self._capacity = (records, bytes_)
        self._reserve = (live_records, live_bytes)
        self._records = 0
        self._bytes = 0
        self._request_interval_s = request_interval_s
        self._next_request = 0.0

    @property
    def used(self) -> tuple[int, int]:
        return self._records, self._bytes

    def try_acquire(
        self, kind: Literal["live", "backfill"], records: int, bytes_: int
    ) -> Reservation | None:
        integer(records, 1)
        integer(bytes_, 0)
        if kind not in ("live", "backfill"):
            raise ValueError("Invalid budget lane")
        limit_records, limit_bytes = self._capacity
        if kind == "backfill":
            limit_records -= self._reserve[0]
            limit_bytes -= self._reserve[1]
        if (
            self._records + records > limit_records
            or self._bytes + bytes_ > limit_bytes
        ):
            return None
        self._records += records
        self._bytes += bytes_
        return Reservation(self, records, bytes_)

    def admit_request(self) -> bool:
        """One shared monotonic request/start pacing gate, no unbounded waiters."""
        now = time.monotonic()
        if now < self._next_request:
            return False
        self._next_request = now + self._request_interval_s
        return True
