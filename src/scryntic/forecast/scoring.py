"""Append-only outcome and score vintages over the frozen forecast reference."""

from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal, localcontext
from typing import Any, cast

from scryntic.domain.validation import integer
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import (
    FINAL_START_NS,
    ForecastContent,
    ForecastSlot,
    identity,
)
from scryntic.research.targets import ISSUE_DELAY_NS, HourlySeries


def _number(value: Decimal) -> str:
    return "0" if value == 0 else str(value)


def _unavailable_state(reason: str) -> str:
    if reason == "not-yet-available":
        return "pending"
    if reason == "missing-constituent":
        return "missing"
    return "invalid"


def _check_source(slot: ForecastSlot, series: HourlySeries) -> None:
    key = series.key
    if (key.source_id, key.venue, key.category, key.instrument) != (
        slot.stream.source_id,
        slot.venue,
        slot.category,
        slot.instrument,
    ):
        raise ValueError("PI04 source/instrument differs from the frozen stream")
    if (
        slot.stream.evidence_class == "historical-reconstruction"
        and key.availability_plane != "historical-reconstruction"
    ):
        raise ValueError("PI04 historical evidence plane differs")
    if (
        slot.stream.evidence_class == "simulated-as-observed"
        and key.availability_plane != "as-observed"
    ):
        raise ValueError("PI04 replay lacks observed-time fixtures")
    if (
        slot.stream.evidence_class == "forward-shadow"
        and key.availability_plane != "as-observed"
    ):
        raise ValueError("PI04 forward outcomes need observed availability")


def prepare_content(slot: ForecastSlot, series: HourlySeries) -> ForecastContent:
    slot.__post_init__()
    _check_source(slot, series)
    origin = series.origin(
        slot.origin_ns,
        slot.horizon_seconds,
        identity(asdict(slot.stream)),
        lookback_hours=1,
    )
    if (
        not origin.eligible
        or origin.origin_close is None
        or origin.input_version_key is None
        or origin.feature_snapshot_sha256 is None
    ):
        raise ValueError("PI04 origin unavailable; record a missing slot")
    return ForecastContent(
        slot,
        origin.feature_snapshot_sha256,
        origin.input_version_key,
        origin.origin_close,
        origin.known_at_ns or slot.origin_ns,
        Decimal(0),
    )


class ForecastScores:
    def __init__(self, ledger: ForecastLedger) -> None:
        self.ledger = ledger

    def _state(
        self, slot: ForecastSlot, state: str, reason: str, as_of_ns: int
    ) -> dict[str, Any]:
        body = {
            "slot": asdict(slot),
            "state": state,
            "reason": reason,
            "as_of_ns": as_of_ns,
        }
        if not any(
            e["subject"] == slot.sha256 and e["body"] == body
            for e in self.ledger.events("slot")
        ):
            self.ledger._append("slot", slot.sha256, body)
        return body

    def mature(
        self, slot_sha256: str, series: HourlySeries, as_of_ns: int
    ) -> dict[str, Any]:
        integer(as_of_ns, 0)
        now = self.ledger.clock.sample().wall_time_ns
        if as_of_ns > now:
            raise ValueError(
                "New PI04 scoring must use the current owner as-of; use view for past vintages"
            )
        as_of_ns = now
        published = self.ledger.publication(slot_sha256)
        content = ForecastContent.from_body(published["content"])
        slot = content.slot
        receipt = self.ledger.receipt(slot_sha256)
        state = self._pre_score_state(content, series, receipt, as_of_ns)
        if state is not None:
            return state
        assert content.point is not None  # Established by the admission checks.
        expected = slot.endpoint_ns + ISSUE_DELAY_NS
        current_origin = series.origin(
            slot.origin_ns,
            slot.horizon_seconds,
            identity(asdict(slot.stream)),
            lookback_hours=1,
        )
        frozen = replace(
            current_origin,
            input_version_key=content.reference_sha256,
            feature_snapshot_sha256=content.input_sha256,
            origin_close=content.reference_close,
            eligible=True,
            reason=None,
        )
        with localcontext() as context:
            context.prec = 28
            outcome = series.outcome(
                frozen,
                as_of_ns if series.key.availability_plane == "as-observed" else None,
            )
            if outcome.value is None or outcome.version_key is None:
                reason = outcome.reason or "unavailable"
                return self._state(slot, _unavailable_state(reason), reason, as_of_ns)
            if outcome.matured_at_ns is not None and outcome.matured_at_ns > as_of_ns:
                return self._state(slot, "pending", "not-yet-available", as_of_ns)
            vintages = [
                e["body"]
                for e in self.ledger.events("outcome")
                if e["subject"] == slot_sha256
            ]
            previous = next(
                (v for v in vintages if v["outcome_sha256"] == outcome.version_key),
                None,
            )
            truth = identity(
                {
                    "source": slot.stream.source_id,
                    "evidence_class": slot.stream.evidence_class,
                    "venue": slot.venue,
                    "category": slot.category,
                    "instrument": slot.instrument,
                    "origin_ns": slot.origin_ns,
                    "horizon_seconds": slot.horizon_seconds,
                    "target": slot.target,
                    "reference_close": str(content.reference_close),
                    "lineage_sha256": outcome.raw_lineage_sha256,
                    "value": str(outcome.value),
                }
            )
            if previous is None:
                body = {
                    "publication_sha256": content.sha256,
                    "outcome_sha256": outcome.version_key,
                    "truth_sha256": truth,
                    "value": str(outcome.value),
                    "lineage_sha256": outcome.raw_lineage_sha256,
                    "expected_maturity_ns": expected,
                    "source_available_ns": outcome.matured_at_ns,
                    "observed_as_of_ns": as_of_ns,
                    "supersedes": vintages[-1]["outcome_sha256"] if vintages else None,
                }
                self.ledger._append("outcome", slot_sha256, body)
            # A vintage keeps its original first score-as-of across repeated calls.
            existing = [
                e["body"]
                for e in self.ledger.events("score")
                if e["subject"] == slot_sha256
                and e["body"]["outcome_sha256"] == outcome.version_key
            ]
            if existing:
                return cast(dict[str, Any], existing[0])
            error = content.point - outcome.value
            score = {
                "state": "scored",
                "publication_sha256": content.sha256,
                "outcome_sha256": outcome.version_key,
                "truth_sha256": truth,
                "scorer": "pi04-point-decimal28-v1",
                "as_of_ns": as_of_ns,
                "absolute_error": str(abs(error)),
                "squared_error": str(error * error),
                "magnitude_error": str(abs(abs(content.point) - abs(outcome.value))),
                "direction_correct": content.point.compare(0)
                == outcome.value.compare(0),
                "zero_prediction": content.point == 0,
                "zero_outcome": outcome.value == 0,
                "evidence_class": receipt["evidence_class"],
            }
            self.ledger._append("score", slot_sha256, score)
            return score

    def _pre_score_state(
        self,
        content: ForecastContent,
        series: HourlySeries,
        receipt: dict[str, Any],
        as_of_ns: int,
    ) -> dict[str, Any] | None:
        slot = content.slot
        if (
            slot.stream.evidence_class == "forward-shadow"
            and slot.endpoint_ns >= FINAL_START_NS
        ):
            return self._state(slot, "sealed", "final-outcome-access-denied", as_of_ns)
        _check_source(slot, series)
        if (
            slot.stream.evidence_class == "forward-shadow"
            and receipt["evidence_class"] != "genuine-forward"
        ):
            return self._state(slot, "excluded", receipt["evidence_class"], as_of_ns)
        if (
            receipt["timeliness"] != "on-time"
            and slot.stream.evidence_class == "synthetic-fixture"
        ):
            return self._state(
                slot, "excluded", "publication-" + receipt["timeliness"], as_of_ns
            )
        if content.point is None:
            return self._state(
                slot, "abstained", content.reason or "abstained", as_of_ns
            )
        expected = slot.endpoint_ns + ISSUE_DELAY_NS
        if as_of_ns < expected:
            return self._state(slot, "pending", "not-mature", as_of_ns)
        return None

    def view(
        self, slot_sha256: str, as_of_ns: int, *, first_available: bool = False
    ) -> dict[str, Any] | None:
        integer(as_of_ns, 0)
        records = [
            e["body"]
            for e in self.ledger.events("score")
            if e["subject"] == slot_sha256 and e["body"]["as_of_ns"] <= as_of_ns
        ]
        if not records:
            return None
        return cast(dict[str, Any], records[0] if first_available else records[-1])

    def cohort(
        self, as_of_ns: int, *, first_available: bool = False
    ) -> tuple[dict[str, Any], ...]:
        integer(as_of_ns, 0)
        rows: dict[str, dict[str, Any]] = {}
        for event in self.ledger.events("slot"):
            body = event["body"]
            if body.get("as_of_ns", 2**63) <= as_of_ns:
                rows[event["subject"]] = {
                    "slot_sha256": event["subject"],
                    "slot": body["slot"],
                    "state": body["state"],
                    "reason": body.get("reason"),
                    "as_of_ns": body["as_of_ns"],
                    "publication": None,
                    "receipt": None,
                    "score": None,
                }
        for event in self.ledger.events("publication"):
            row = self._publication_row(
                event, rows.get(event["subject"]), as_of_ns, first_available
            )
            if row is not None:
                rows[event["subject"]] = row
        return tuple(rows[key] for key in sorted(rows))

    def _publication_row(
        self,
        event: dict[str, Any],
        prior: dict[str, Any] | None,
        as_of_ns: int,
        first_available: bool,
    ) -> dict[str, Any] | None:
        content = ForecastContent.from_body(event["body"]["content"])
        receipt = self.ledger.receipt(event["subject"])
        observed = receipt["post_commit_observation"]
        if observed is None or observed["wall_time_ns"] > as_of_ns:
            return None
        score = self.view(event["subject"], as_of_ns, first_available=first_available)
        state, reason = self._cohort_state(content, receipt, score, prior)
        return {
            "slot_sha256": event["subject"],
            "slot": asdict(content.slot),
            "state": state,
            "reason": reason,
            "publication": event["body"],
            "receipt": receipt,
            "score": score,
            "as_of_ns": max(
                observed["wall_time_ns"], prior["as_of_ns"] if prior else 0
            ),
        }

    @staticmethod
    def _cohort_state(
        content: ForecastContent,
        receipt: dict[str, Any],
        score: dict[str, Any] | None,
        prior: dict[str, Any] | None,
    ) -> tuple[str, str | None]:
        state = "pending"
        if score:
            state = "scored"
        elif content.point is None:
            state = "abstained"
        reason = content.reason
        if prior is not None and score is None:
            state, reason = prior["state"], prior["reason"]
        if (
            content.slot.stream.evidence_class
            in ("synthetic-fixture", "forward-shadow")
            and receipt["timeliness"] != "on-time"
        ):
            state = "late" if receipt["timeliness"] == "late" else "unclassifiable"
            reason = receipt["timeliness"]
        if (
            content.slot.stream.evidence_class == "forward-shadow"
            and receipt["evidence_class"] != "genuine-forward"
        ):
            state = (
                "late"
                if receipt["evidence_class"] == "late-forward"
                else "unclassifiable"
            )
            reason = receipt["evidence_class"]
        return state, reason

    def compare(
        self, left: str, right: str, as_of_ns: int, *, first_available: bool = False
    ) -> dict[str, Any]:
        if left == right:
            raise ValueError("PI04 comparison needs two distinct registered streams")
        pairs: dict[str, dict[str, tuple[ForecastContent, dict[str, Any]]]] = {
            left: {},
            right: {},
        }
        counts: dict[str, dict[str, int]] = {left: {}, right: {}}
        dimensions: dict[str, dict[str, dict[str, int]]] = {left: {}, right: {}}
        for row in self.cohort(as_of_ns, first_available=first_available):
            stream = row["slot"]["stream"]["stream_id"]
            if stream not in pairs:
                continue
            view, state = row["score"], row["state"]
            counts[stream][state] = counts[stream].get(state, 0) + 1
            dimension = (
                str(row["slot"]["horizon_seconds"]) + ":" + row["slot"]["instrument"]
            )
            dc = dimensions[stream].setdefault(dimension, {})
            dc[state] = dc.get(state, 0) + 1
            if view is not None and state == "scored":
                content = ForecastContent.from_body(row["publication"]["content"])
                pairs[stream][view["truth_sha256"]] = (content, view)
        common = sorted(pairs[left].keys() & pairs[right].keys())
        horizon: dict[str, dict[str, Any]] = {}
        differences = []
        for key in common:
            lc, ls = pairs[left][key]
            _, rs = pairs[right][key]
            delta = Decimal(ls["absolute_error"]) - Decimal(rs["absolute_error"])
            differences.append(delta)
            h = str(lc.slot.horizon_seconds)
            entry = horizon.setdefault(
                h, {"paired": 0, "absolute_error_difference_sum": Decimal(0)}
            )
            entry["paired"] += 1
            entry["absolute_error_difference_sum"] += delta
        for entry in horizon.values():
            entry["mae_difference"] = _number(
                entry.pop("absolute_error_difference_sum") / entry["paired"]
            )
        return {
            "left": left,
            "right": right,
            "as_of_ns": as_of_ns,
            "paired": len(common),
            "mae_difference": _number(sum(differences, Decimal(0)) / len(common))
            if common
            else None,
            "horizons": horizon,
            "counts": counts,
            "counts_by_horizon_instrument": dimensions,
            "claims": "descriptive-only-no-promotion",
        }
