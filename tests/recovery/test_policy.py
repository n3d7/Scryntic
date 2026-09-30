"""Family semantics and restart conservatism, using the real sole writer."""

from pathlib import Path

import pytest

from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.ingestion.sqlite_spool import DurableIngestor, IngestionError
from scryntic.recovery.coverage import CoverageLedger, Family, RecoveryStream
from tests.ingestion.test_sqlite_spool import installation


def sample() -> ClockSample:
    return ClockSample(100, 100, "boot-a", TimeQuality("clock-a", "unknown"))


@pytest.mark.parametrize(
    "family,status",
    [
        (Family.CANDLE, "pending"),
        (Family.TRADE, "unrecoverable"),
        (Family.BOOK, "unrecoverable"),
        (Family.PERIODIC, "unknown"),
        (Family.UNKNOWN, "unknown"),
    ],
)
def test_family_loss_is_visible(tmp_path: Path, family: Family, status: str) -> None:
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=1,
        max_payload_bytes=1024,
    ) as spool:
        ledger = CoverageLedger(
            spool,
            RecoveryStream(
                "test-a",
                "fake",
                "events",
                "channel",
                None,
                family,
                "backfill" if family is Family.CANDLE else "coverage-loss",
            ),
            baseline_ns=0,
        )
        ledger.begin(sample())
        ledger.loss(10, 20, "overflow", sample())
        assert ledger.snapshot.spans[-1].status == status
        assert ledger.coverage(10, 20) == status
        assert not ledger.snapshot.book_valid
        assert ledger.snapshot.spans[-1].detected.quality.status == "unknown"


def test_missing_loss_marker_recovers_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installation(tmp_path)
    context = RecoveryStream(
        "test-a", "fake", "events", "channel", None, Family.CANDLE, "backfill"
    )
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=1, max_payload_bytes=1024
    ) as spool:
        ledger = CoverageLedger(spool, context, baseline_ns=10)
        ledger.begin(sample())
        with monkeypatch.context() as patch:
            patch.setattr(
                spool,
                "recovery_put",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    IngestionError("injected")
                ),
            )
            with pytest.raises(IngestionError):
                ledger.loss(10, 20, "overflow", sample())
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=1, max_payload_bytes=1024
    ) as spool:
        ledger = CoverageLedger(spool, context, baseline_ns=10)
        ledger.begin(sample())
        assert ledger.snapshot.spans[-1].status == "unknown"
        assert ledger.snapshot.spans[-1].start_ns == 10
        assert ledger.snapshot.spans[-1].end_ns is None


def test_book_resnapshot_does_not_erase_gap(tmp_path: Path) -> None:
    with DurableIngestor(
        installation(tmp_path),
        producer="a",
        epoch="a",
        capacity=1,
        max_payload_bytes=1024,
    ) as spool:
        ledger = CoverageLedger(
            spool,
            RecoveryStream(
                "test-a", "fake", "books", "channel", None, Family.BOOK, "resnapshot"
            ),
            baseline_ns=0,
        )
        ledger.begin(sample())
        ledger.resnapshot(100)
        assert ledger.book_delta(101, sample())
        assert not ledger.book_delta(103, sample())
        assert not ledger.snapshot.book_valid
        assert not ledger.book_delta(104, sample())
        ledger.resnapshot(200)
        assert ledger.book_delta(201, sample())
        assert ledger.snapshot.spans[-1].status == "unrecoverable"


def test_loss_quota_retains_restart_unknown_headroom(tmp_path: Path) -> None:
    target = installation(tmp_path)
    context = RecoveryStream(
        "test-a", "fake", "events", "channel", None, Family.UNKNOWN, "coverage-loss"
    )
    with DurableIngestor(
        target, producer="a", epoch="a", capacity=1, max_payload_bytes=1024
    ) as spool:
        ledger = CoverageLedger(spool, context, baseline_ns=0)
        ledger.begin(sample())
        for index in range(63):
            ledger.loss(index, index + 1, "overflow", sample())
        with pytest.raises(IngestionError, match="quota"):
            ledger.loss(100, 101, "overflow", sample())
    with DurableIngestor(
        target, producer="a", epoch="b", capacity=1, max_payload_bytes=1024
    ) as spool:
        ledger = CoverageLedger(spool, context, baseline_ns=0)
        ledger.begin(sample())
        assert ledger.snapshot.unknown_since_ns == 0
        assert len(ledger.snapshot.spans) == 63
