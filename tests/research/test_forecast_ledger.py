"""Publication timing is independent of worker timestamps and receipt fsync."""

import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.forecast.ledger import ForecastLedger
from scryntic.forecast.ledger_contracts import ForecastContent, ForecastSlot, Stream
from scryntic.research.targets import HOUR_NS, ISSUE_DELAY_NS
from tests.normalization.helpers import installation

T = 1704067200 * 10**9  # 2024-01-01, outside the sealed final reservation.


class Clock:
    def __init__(self, now: int = T + ISSUE_DELAY_NS) -> None:
        self.now = now
        self.uncertainty = 0
        self.session = "fixture-boot"
        self.quality: Literal["healthy", "unknown", "degraded"] = "healthy"

    def sample(self) -> ClockSample:
        return ClockSample(
            self.now,
            self.now,
            self.session,
            TimeQuality("fixture-clock", self.quality, 0, self.uncertainty, 0),
        )


def content(value: str = "0") -> ForecastContent:
    stream = Stream("fixture-zero", "synthetic-fixture", "synthetic-hourly")
    slot = ForecastSlot(stream, "Fixture", "linear", "BTCUSDT", T, 3600)
    return ForecastContent(slot, "a" * 64, "b" * 64, Decimal("100"), T, Decimal(value))


def test_same_content_retries_preserve_receipt_and_conflicts_are_explicit(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    with ForecastLedger(target, Clock()) as ledger:
        first = ledger.publish(content(), attempt_id="1" * 32)
        assert first["timeliness"] == "on-time"
        assert first["evidence_class"] == "synthetic-fixture"
        assert ledger.publish(content(), attempt_id="2" * 32) == first
        conflict = ledger.publish(
            replace(content(), input_sha256="c" * 64), attempt_id="3" * 32
        )
        assert conflict["disposition"] == "conflict"
        assert conflict["accepted_content_sha256"] == content().sha256
        assert (
            ledger.publication(content().slot.sha256)["content_sha256"]
            == content().sha256
        )
    with ForecastLedger(target, Clock(T + 2 * HOUR_NS)) as ledger:
        assert ledger.publish(content(), attempt_id="4" * 32) == first
        assert ledger.status()["conflicts"] == 1


@pytest.mark.parametrize(
    ("delta", "uncertainty", "quality", "expected"),
    [
        (0, 0, "healthy", "on-time"),
        (0, 1, "healthy", "uncertain"),
        (1, 0, "healthy", "late"),
        (-1, 1, "healthy", "on-time"),
        (-1, 0, "unknown", "unknown"),
    ],
)
def test_inclusive_deadline_uses_conservative_post_commit_bound(
    tmp_path: Path,
    delta: int,
    uncertainty: int,
    quality: Literal["healthy", "unknown", "degraded"],
    expected: str,
) -> None:
    clock = Clock(T + ISSUE_DELAY_NS + delta)
    clock.uncertainty, clock.quality = uncertainty, quality
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        receipt = ledger.publish(content(), attempt_id="1" * 32)
        assert receipt["timeliness"] == expected
        assert receipt["evidence_class"] != "genuine-forward"


def test_slow_commit_crossing_deadline_is_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(T + ISSUE_DELAY_NS - 1)
    with ForecastLedger(installation(tmp_path), clock) as ledger:
        original = ledger._append

        def slow(kind: str, subject: str, body: dict[str, object]) -> str:
            result = original(kind, subject, body)
            if kind == "publication":
                clock.now = T + ISSUE_DELAY_NS + 1
            return result

        monkeypatch.setattr(ledger, "_append", slow)
        assert ledger.publish(content(), attempt_id="1" * 32)["timeliness"] == "late"


def test_crash_after_publication_never_backdates_missing_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    with ForecastLedger(target, Clock()) as ledger:
        original = ledger._append

        def crash(kind: str, subject: str, body: dict[str, object]) -> str:
            if kind == "receipt":
                raise RuntimeError("injected process death before certificate")
            return original(kind, subject, body)

        monkeypatch.setattr(ledger, "_append", crash)
        with pytest.raises(RuntimeError):
            ledger.publish(content(), attempt_id="1" * 32)
    with ForecastLedger(target, Clock(T + 2 * HOUR_NS)) as ledger:
        receipt = ledger.publish(content(), attempt_id="2" * 32)
        assert receipt["timeliness"] == "recovery-unknown"
        assert receipt["evidence_class"] == "synthetic-fixture"


def test_live_admission_and_final_overlap_fail_closed() -> None:
    with pytest.raises(ValueError):
        content("1")
    with pytest.raises(ValueError):
        Stream("live-zero", "genuine-forward", "binance-api")
    with pytest.raises(ValueError):
        replace(content().slot, origin_ns=1793491200 * 10**9)  # final start
    with pytest.raises(ValueError):
        replace(content().slot, origin_ns=1793404800 * 10**9, horizon_seconds=604800)


def test_real_process_death_after_commit_retains_uncertified_publication(
    tmp_path: Path,
) -> None:
    target = installation(tmp_path)
    script = """
import os, sys
from pathlib import Path
from tests.normalization.helpers import installation
from tests.research.test_forecast_ledger import Clock, content
from scryntic.forecast.ledger import ForecastLedger
with ForecastLedger(installation(Path(sys.argv[1])), Clock()) as ledger:
    append = ledger._append
    def crash(kind, subject, body):
        if kind == "receipt": os._exit(73)
        return append(kind, subject, body)
    ledger._append = crash
    ledger.publish(content(), attempt_id="1" * 32)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        timeout=10,
        check=False,
    )
    assert completed.returncode == 73
    with ForecastLedger(target, Clock()) as ledger:
        assert ledger.status()["publications"] == 1
        assert ledger.receipt(content().slot.sha256)["timeliness"] == "recovery-unknown"


def test_corrupt_content_and_second_owner_fail_closed(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with ForecastLedger(target, Clock()) as ledger:
        ledger.publish(content(), attempt_id="1" * 32)
        with pytest.raises(BlockingIOError):
            ForecastLedger(target, Clock())
    with sqlite3.connect(target.state_dir / "forecast-ledger.sqlite3") as connection:
        connection.execute("UPDATE events SET payload=? WHERE sequence=1", (b"{}",))
    with pytest.raises(ValueError):
        ForecastLedger(target, Clock())


def test_committed_prefix_loss_is_detected_by_retained_head(tmp_path: Path) -> None:
    target = installation(tmp_path)
    with ForecastLedger(target, Clock()) as ledger:
        ledger.publish(content(), attempt_id="1" * 32)
    with sqlite3.connect(target.state_dir / "forecast-ledger.sqlite3") as connection:
        connection.execute(
            "DELETE FROM events WHERE sequence=(SELECT max(sequence) FROM events)"
        )
    with pytest.raises(ValueError):
        ForecastLedger(target, Clock())
