import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.forecast.inference import InferenceService, InferenceStore
from scryntic.forecast.ledger import ForecastLedger
from scryntic.jobs.contracts import JobState
from tests.normalization.helpers import installation
from tests.research.test_forecast_ledger import Clock, T, content


def test_scheduler_restarts_without_backfilling_or_reclaiming_fit_budget(
    tmp_path: Path,
) -> None:
    target, clock = installation(tmp_path), Clock(T + 1)
    with (
        ForecastLedger(target, clock) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        service = InferenceService(store, ledger)
        records = service.schedule((content(),))
        result = asyncio.run(service.run(records[0].job.job_id))
        assert result.state is JobState.SUCCEEDED
        assert (
            ledger.receipt(content().slot.sha256)["evidence_class"]
            == "synthetic-fixture"
        )
        assert not (target.state_dir / "research-ledger.jsonl").exists()
        assert service.schedule((content(),))[0] == result
    clock.now = T + 4 * 3600 * 10**9
    with (
        ForecastLedger(target, clock) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        service = InferenceService(store, ledger)
        future = replace(
            content(), slot=replace(content().slot, origin_ns=T + 3600 * 10**9)
        )
        assert service.schedule((future,)) == ()
        assert ledger.events("slot")[-1]["body"]["state"] == "missed"
        assert ledger.status()["publications"] == 1


def test_cancelled_or_stale_attempt_cannot_publish(tmp_path: Path) -> None:
    target, clock = installation(tmp_path), Clock(T + 1)
    with (
        ForecastLedger(target, clock) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        service = InferenceService(store, ledger)
        queued = service.schedule((content(),))[0]
        active = store.claim(queued.job.job_id)
        data = asyncio.run(service.executor.execute(active.execution()))
        service.cancel(queued.job.job_id)
        service._accept(active, data)
        assert store.get(queued.job.job_id).state is JobState.CANCELLED
        assert ledger.status()["publications"] == 0


def test_scheduler_denies_unbounded_batches_and_input_conflict(tmp_path: Path) -> None:
    target, clock = installation(tmp_path), Clock(T + 1)
    with (
        ForecastLedger(target, clock) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        service = InferenceService(store, ledger)
        forecast = content()
        oversized = (forecast,) * 65
        conflicting = (replace(forecast, input_sha256="c" * 64),)
        with pytest.raises(ValueError):
            service.schedule(oversized)
        service.schedule((forecast,))
        with pytest.raises(ValueError):
            service.schedule(conflicting)


def test_inference_preserves_an_existing_fit_ledger_byte_for_byte(
    tmp_path: Path,
) -> None:
    from scryntic.research.ledger import ResearchLedger, Reservation

    target, clock = installation(tmp_path), Clock(T + 1)
    with ResearchLedger(target, clock=lambda: T) as research:
        research.reserve(Reservation("a" * 64, T + 100, T + 200, T + 90))
        for i in range(5):
            research.trial(f"fit-{i}", "b" * 64, "registered", 0)
            research.trial(f"fit-{i}", "b" * 64, "started", 1)
            research.trial(f"fit-{i}", "b" * 64, "succeeded", 1)
    paths = (
        target.state_dir / "research-ledger.jsonl",
        target.state_dir / "research-ledger.anchor",
    )
    before = tuple(p.read_bytes() for p in paths)
    with (
        ForecastLedger(target, clock) as ledger,
        InferenceStore(target, ledger, clock=lambda: clock.now) as store,
    ):
        session = InferenceService(store, ledger)
        asyncio.run(session.infer_due((content(),)))
    assert tuple(p.read_bytes() for p in paths) == before
