import asyncio
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.jobs.contracts import JobAttempt
from scryntic.research.ledger import ResearchDenied, ResearchLedger, Reservation
from tests.jobs.helpers import job
from tests.jobs.test_execution import services


def test_existing_job_service_denies_reserved_inputs_even_with_custom_reader(
    tmp_path: Path,
) -> None:
    root, store, _, service = services(tmp_path)
    try:
        with ResearchLedger(root) as ledger:
            ledger.reserve(
                Reservation(
                    "a" * 64, time.time_ns() + 10**15, time.time_ns() + 2 * 10**15, 1
                )
            )
        request = job()
        with pytest.raises(ResearchDenied):
            service.submit_job(request)
    finally:
        asyncio.run(service.aclose())
        store.close()


def test_trial_and_intervention_events_follow_durable_job_execution(
    tmp_path: Path,
) -> None:
    root, store, _, service = services(tmp_path)
    try:
        end = time.time_ns() + 10**15
        with ResearchLedger(root) as ledger:
            ledger.reserve(Reservation("a" * 64, end, end + 10**15, end))
        service.submit_job(job())
        asyncio.run(service.run("job-a"))
        with ResearchLedger(root) as ledger:
            phases = [e["body"]["phase"] for e in ledger.events if e["kind"] == "trial"]
            assert phases == ["registered", "started", "succeeded"]
        other = job()
        service.submit_job(
            replace(other, forecast=replace(other.forecast, request_id="job-b"))
        )
        service.cancel("job-b")
        with ResearchLedger(root) as ledger:
            assert [e["body"]["phase"] for e in ledger.events if e["kind"] == "trial"][
                -3:
            ] == ["registered", "intervention", "cancelled"]
    finally:
        asyncio.run(service.aclose())
        store.close()


def test_job_restart_records_interruption_without_resetting_job_identity(
    tmp_path: Path,
) -> None:
    from scryntic.jobs.store import JobStore
    from tests.jobs.helpers import NOW

    root, store, _, service = services(tmp_path)
    end = time.time_ns() + 10**15
    with ResearchLedger(root) as ledger:
        ledger.reserve(Reservation("a" * 64, end, end + 10**15, end))
    service.submit_job(job())
    active = store.claim("job-a")
    with ResearchLedger(root) as ledger:
        ledger.trial(active.job.job_id, active.job.sha256, "started", active.attempt)
    asyncio.run(service.aclose())
    store.close()
    with JobStore(root, clock=lambda: NOW) as reopened:
        assert reopened.get("job-a").state.value == "interrupted"
        assert reopened.get("job-a").job.sha256 == active.job.sha256
    with ResearchLedger(root) as ledger:
        phases = [e["body"]["phase"] for e in ledger.events if e["kind"] == "trial"]
        assert phases[-1] == "interrupted"


def test_committed_terminal_state_reconciles_after_audit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scryntic.jobs.contracts import JobState
    from scryntic.jobs.store import JobStore
    from tests.jobs.helpers import NOW

    root, store, _, service = services(tmp_path)
    end = time.time_ns() + 10**15
    with ResearchLedger(root) as ledger:
        ledger.reserve(Reservation("a" * 64, end, end + 10**15, end))
    service.submit_job(job())
    active = store.claim("job-a")
    original = ResearchLedger.trial

    def fail(
        self: ResearchLedger,
        trial_id: str,
        config_sha256: str,
        phase: str,
        attempt: int,
    ) -> None:
        if phase == "failed":
            raise OSError("injected audit failure")
        return original(self, trial_id, config_sha256, phase, attempt)

    with monkeypatch.context() as patch:
        patch.setattr(ResearchLedger, "trial", fail)
        with pytest.raises(OSError):
            store.finish(active, JobState.FAILED, reason="provider_failed")
    asyncio.run(service.aclose())
    store.close()
    with JobStore(root, clock=lambda: NOW) as reopened:
        assert reopened.get("job-a").state.value == "failed"
    with ResearchLedger(root) as ledger:
        assert any(
            e["kind"] == "trial" and e["body"]["phase"] == "failed"
            for e in ledger.events
        )


def test_read_triggered_expiry_is_audited(tmp_path: Path) -> None:
    from tests.jobs.helpers import NOW

    now = [NOW]
    root, store, _, service = services(tmp_path, clock=lambda: now[0])
    end = time.time_ns() + 10**15
    try:
        with ResearchLedger(root) as ledger:
            ledger.reserve(Reservation("a" * 64, end, end + 10**15, end))
        service.submit_job(job())
        now[0] = job().deadline_ns + 1
        assert store.get("job-a").state.value == "deadline_expired"
        with ResearchLedger(root) as ledger:
            assert any(
                e["kind"] == "trial" and e["body"]["phase"] == "deadline_expired"
                for e in ledger.events
            )
    finally:
        asyncio.run(service.aclose())
        store.close()


@pytest.mark.parametrize("broken_phase", ["intervention", "registered"])
@pytest.mark.parametrize("stop_operation", ["cancel", "close"])
def test_worker_cancel_is_not_skipped_when_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken_phase: str,
    stop_operation: str,
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider
    from tests.jobs.helpers import review

    started = asyncio.Event()
    stopped = asyncio.Event()

    class Paused(LocalFakeProvider):
        async def execute(self, request: JobAttempt) -> bytes:
            started.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError("Paused test provider unexpectedly resumed")
            finally:
                stopped.set()

    root, store, _, service = services(tmp_path, provider=Paused(review()))
    end = time.time_ns() + 10**15
    with ResearchLedger(root) as ledger:
        ledger.reserve(Reservation("a" * 64, end, end + 10**15, end))
    original = ResearchLedger.trial

    def fail(
        self: ResearchLedger,
        trial_id: str,
        config_sha256: str,
        phase: str,
        attempt: int,
    ) -> None:
        if phase in (broken_phase, "cancelled", "interrupted"):
            raise OSError("injected audit failure")
        return original(self, trial_id, config_sha256, phase, attempt)

    async def exercise() -> None:
        service.submit_job(job())
        running = asyncio.create_task(service.run("job-a"))
        await asyncio.wait_for(started.wait(), 2)
        with monkeypatch.context() as patch:
            patch.setattr(ResearchLedger, "trial", fail)
            if stop_operation == "close":
                with pytest.raises(OSError):
                    await service.aclose()
            else:
                with pytest.raises(OSError):
                    service.cancel("job-a")
            try:
                await asyncio.wait_for(stopped.wait(), 0.1)
            finally:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        await service.aclose()

    try:
        asyncio.run(exercise())
    finally:
        store.close()
