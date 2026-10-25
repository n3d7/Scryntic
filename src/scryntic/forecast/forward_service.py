"""Finite shadow scheduling and maturation; all authority stays with the owner."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from scryntic.forecast.inference import InferenceService, InferenceStore
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import (
    FINAL_START_NS,
    FORWARD_SOURCE,
    ForecastContent,
    ForecastSlot,
    Stream,
    identity,
)
from scryntic.forecast.live import BinanceHourly
from scryntic.forecast.scoring import ForecastScores
from scryntic.research.targets import (
    HORIZONS_SECONDS,
    HOUR_NS,
    ISSUE_DELAY_NS,
    HourlyBar,
    HourlySeries,
    SeriesKey,
)


class ForwardSession:
    def __init__(
        self,
        store: InferenceStore,
        ledger: ForecastLedger,
        source: BinanceHourly | None = None,
    ) -> None:
        if ledger.admission is None:
            raise ValueError("PI04 session requires owner admission")
        self.store, self.ledger = store, ledger
        self.source = source or BinanceHourly(ledger)
        self.inference = InferenceService(store, ledger)
        self.stream = Stream("binance-zero-v1", "forward-shadow", FORWARD_SOURCE)

    def _mark(self, slot: ForecastSlot, state: str, reason: str) -> None:
        body = {
            "slot": asdict(slot),
            "state": state,
            "reason": reason,
            "as_of_ns": self.ledger.clock.sample().wall_time_ns,
        }
        if not any(
            e["subject"] == slot.sha256 and e["body"]["state"] == state
            for e in self.ledger.events("slot")
        ):
            self.ledger._append("slot", slot.sha256, body)

    async def tick(self, origin_ns: int) -> dict[str, Any]:
        counts = {"published": 0, "missed": 0, "missing": 0, "conflicts": 0}
        for instrument in ("BTCUSDT", "ETHUSDT"):
            result = await self._tick_instrument(instrument, origin_ns)
            for key in counts:
                counts[key] += result[key]
        return {
            **counts,
            "origin_ns": origin_ns,
            "evidence": "inspect-publication-receipts",
        }

    async def _tick_instrument(self, instrument: str, origin_ns: int) -> dict[str, int]:
        counts = {"published": 0, "missed": 0, "missing": 0, "conflicts": 0}
        horizons = HORIZONS_SECONDS if origin_ns % (24 * HOUR_NS) == 0 else (3600,)
        slots = tuple(
            ForecastSlot(
                self.stream, "Binance", "USD-M-futures", instrument, origin_ns, h
            )
            for h in horizons
        )
        known = {e["subject"] for e in self.ledger.events("publication")}
        pending = tuple(slot for slot in slots if slot.sha256 not in known)
        counts["published"] = len(slots) - len(pending)
        if not pending:
            return counts
        if self.ledger.clock.sample().quality.status != "healthy":
            for slot in pending:
                self._mark(slot, "missing", "unqualified-clock")
            counts["missing"] = len(pending)
            return counts
        if self.store.now() > origin_ns + ISSUE_DELAY_NS:
            for slot in pending:
                self._mark(slot, "missed", "deadline")
            counts["missed"] = len(pending)
            return counts
        try:
            value = await self.source.fetch_origin(instrument, origin_ns)
            contents = tuple(self.source.content(slot, value) for slot in pending)
            await self.inference.infer_due(contents)
            known = {e["subject"] for e in self.ledger.events("publication")}
            counts["published"] += sum(slot.sha256 in known for slot in pending)
        except (OSError, ValueError):
            for slot in pending:
                self._mark(slot, "missing", "source-or-clock-unavailable")
            counts["missing"] = len(pending)
        return counts

    async def run(self, wait_seconds: int = 0) -> dict[str, Any]:
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
            raise ValueError("PI04 finite schedule wait bound")
        now = self.store.now()
        origin = now // HOUR_NS * HOUR_NS
        if wait_seconds and now > origin + ISSUE_DELAY_NS:
            origin += HOUR_NS
        stop = time.monotonic() + wait_seconds
        try:
            while self.store.now() < origin:
                if time.monotonic() >= stop:
                    return {"state": "waiting", "origin_ns": origin}
                self.ledger.clock.sample()  # Keep continuity checks active while waiting.
                await asyncio.sleep(min(1.0, max(0, stop - time.monotonic())))
            clock_stop = time.monotonic() + 60
            while (
                self.ledger.clock.sample().quality.status != "healthy"
                and self.store.now() < origin + ISSUE_DELAY_NS
            ):
                if time.monotonic() >= clock_stop:
                    break
                await asyncio.sleep(1)
            # Bounded retries only while the registered publication window is open.
            result = await self.tick(origin)
            for _ in range(2):
                if not result["missing"] or self.store.now() >= origin + ISSUE_DELAY_NS:
                    break
                await asyncio.sleep(1)
                result = await self.tick(origin)
            return result
        finally:
            await self.inference.aclose()

    async def mature(self, limit: int = 4) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("PI04 maturation bound")
        publications = [
            e
            for e in self.ledger.events("publication")
            if e["body"]["content"]["slot"]["stream"]["source_id"] == FORWARD_SOURCE
        ]
        if not publications:
            return {"results": [], "fetched_rows": 0, "final_access": False}
        history = self.ledger.events("maturation")
        ordered = self._maturation_order(publications, history)
        results: list[dict[str, Any]] = []
        fetched = 0
        for event in ordered[:limit]:
            result, count = await self._mature_one(event, history, 64 - fetched)
            fetched += count
            results.append({"slot": event["subject"], **result})
            self.ledger._append(
                "maturation",
                identity("pi04-maturation-cursor"),
                {"last_slot": event["subject"], "as_of_ns": self.store.now()},
            )
        return {"results": results, "fetched_rows": fetched, "final_access": False}

    @staticmethod
    def _maturation_order(
        publications: list[dict[str, Any]], history: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cursors = [
            e["body"]["last_slot"]
            for e in history
            if e["subject"] == identity("pi04-maturation-cursor")
        ]
        ids = [e["subject"] for e in publications]
        start = (
            (ids.index(cursors[-1]) + 1) % len(ids)
            if cursors and cursors[-1] in ids
            else 0
        )
        return publications[start:] + publications[:start]

    async def _mature_one(
        self, event: dict[str, Any], history: Sequence[dict[str, Any]], budget: int
    ) -> tuple[dict[str, Any], int]:
        content = ForecastContent.from_body(event["body"]["content"])
        slot = content.slot
        if slot.endpoint_ns >= FINAL_START_NS:
            self._mark(slot, "sealed", "final-outcome-access-denied")
            return {"state": "sealed"}, 0
        if self.store.now() < slot.endpoint_ns + ISSUE_DELAY_NS:
            self._mark(slot, "pending", "not-mature")
            return {"state": "pending"}, 0
        progress = [e["body"] for e in history if e["subject"] == slot.sha256]
        endpoint = (
            progress[-1]["next_endpoint_ns"] if progress else slot.origin_ns + HOUR_NS
        )
        endpoint, fetched, unavailable = await self._scan_rows(slot, endpoint, budget)
        self.ledger._append(
            "maturation",
            slot.sha256,
            {
                "next_endpoint_ns": endpoint
                if endpoint <= slot.endpoint_ns
                else slot.origin_ns + HOUR_NS,
                "as_of_ns": self.store.now(),
            },
        )
        return await self._score_cached(content, unavailable), fetched

    async def _scan_rows(
        self, slot: ForecastSlot, endpoint: int, budget: int
    ) -> tuple[int, int, bool]:
        fetched = 0
        while endpoint <= slot.endpoint_ns and fetched < budget:
            try:
                value = await self.source.fetch_origin(slot.instrument, endpoint)
                if not value["clock_qualified"]:
                    raise ValueError("Unqualified outcome observation clock")
            except (OSError, ValueError):
                self._mark(slot, "missing", "outcome-source-or-clock-unavailable")
                return endpoint, fetched, True
            fetched += 1
            endpoint += HOUR_NS
        return endpoint, fetched, False

    def _cached_series(
        self, content: ForecastContent
    ) -> tuple[HourlySeries, int] | None:
        slot = content.slot
        bars = [
            HourlyBar(
                slot.origin_ns - HOUR_NS,
                content.reference_close,
                content.reference_sha256,
                content.latest_available_ns,
            )
        ]
        for current in range(slot.origin_ns + HOUR_NS, slot.endpoint_ns + 1, HOUR_NS):
            cached = self.source.cached_origin(slot.instrument, current)
            if cached is None:
                return None
            bars.append(
                HourlyBar(
                    current - HOUR_NS,
                    Decimal(cached["close"]),
                    identity(
                        {
                            "reference_sha256": cached["reference_sha256"],
                            "observation_sha256": cached["observation_sha256"],
                        }
                    ),
                    cached["latest_available_ns"],
                )
            )
        key = SeriesKey(
            FORWARD_SOURCE,
            slot.venue,
            slot.category,
            slot.instrument,
            identity([b.raw_sha256 for b in bars]),
            "as-observed",
        )
        return HourlySeries(key, bars), max(b.known_at_ns or 0 for b in bars)

    async def _score_cached(
        self, content: ForecastContent, unavailable: bool
    ) -> dict[str, Any]:
        slot = content.slot
        if unavailable:
            self._mark(slot, "missing", "outcome-unavailable")
            return {"state": "missing"}
        cached = self._cached_series(content)
        if cached is None:
            self._mark(slot, "pending", "bounded-outcome-scan")
            return {"state": "pending"}
        series, latest = cached
        delay = max(0, latest - self.store.now()) / 10**9
        if delay > 1:
            self._mark(slot, "pending", "outcome-clock-bound")
            return {"state": "pending"}
        if delay:
            await asyncio.sleep(delay)
        return ForecastScores(self.ledger).mature(slot.sha256, series, self.store.now())
