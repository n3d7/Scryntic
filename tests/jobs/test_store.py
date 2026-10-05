"""Real SQLite persisted transitions and exclusive ownership."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.jobs.helpers import NOW, job
from tests.normalization.helpers import installation


def test_submission_is_idempotent_but_conflicting_reuse_fails(tmp_path: Path) -> None:
    from scryntic.jobs.store import JobStore

    with JobStore(installation(tmp_path), clock=lambda: NOW) as store:
        value = job()
        first = store.submit(value)
        assert store.submit(value) == first
        assert first.state.value == "queued"
        conflicting = replace(value, seed=1)
        with pytest.raises(ValueError, match="conflict"):
            store.submit(conflicting)


def test_restart_marks_abandoned_attempt_interrupted_and_fences_it(
    tmp_path: Path,
) -> None:
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW) as store:
        store.submit(job())
        first = store.claim("job-a")
        assert first.state.value == "running"
    with JobStore(target, clock=lambda: NOW + 1) as store:
        assert store.get("job-a").state.value == "interrupted"
        second = store.claim("job-a", resume=True)
        assert second.attempt == first.attempt + 1
        assert second.token != first.token
        assert not store.finish(first, "failed", reason="provider_failed")
        assert store.get("job-a").state.value == "running"


@pytest.mark.parametrize("terminal", ["cancelled", "deadline_expired", "failed"])
def test_terminal_states_survive_restart_and_cannot_resume(
    tmp_path: Path, terminal: str
) -> None:
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW) as store:
        store.submit(job())
        active = store.claim("job-a")
        if terminal == "cancelled":
            store.cancel("job-a")
        else:
            assert store.finish(active, terminal, reason=terminal)
    with JobStore(target, clock=lambda: NOW + 1) as store:
        assert store.get("job-a").state.value == terminal
        with pytest.raises(ValueError):
            store.claim("job-a", resume=True)


def test_queued_and_running_deadlines_are_durable(tmp_path: Path) -> None:
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW) as store:
        store.submit(job())
        store.submit(job("job-b"))
        store.claim("job-a")
    with JobStore(target, clock=lambda: NOW + 11_000_000_000) as store:
        assert store.get("job-a").state.value == "deadline_expired"
        assert store.get("job-b").state.value == "deadline_expired"


def test_second_owner_and_job_quota_are_rejected(tmp_path: Path) -> None:
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW, max_jobs=1) as store:
        with pytest.raises(ValueError, match="owned"):
            JobStore(target, clock=lambda: NOW)
        store.submit(job())
        overflow = job("job-b")
        with pytest.raises(ValueError, match="quota"):
            store.submit(overflow)


def test_expired_claim_commits_expiration_and_clock_regression_fails_closed(
    tmp_path: Path,
) -> None:
    from scryntic.jobs.store import JobStore

    current = [NOW]
    target = installation(tmp_path)
    with JobStore(target, clock=lambda: current[0]) as store:
        store.submit(job())
        store.submit(job("job-b"))
        current[0] = NOW + 1
        active = store.claim("job-b")
        current[0] = NOW
        assert store.get("job-b").reason == "clock_regression"
        assert not store.finish(active, "failed", reason="provider_failed")
        current[0] = NOW + 10_000_000_000
        with pytest.raises(ValueError):
            store.claim("job-a")
    with JobStore(target, clock=lambda: current[0]) as reopened:
        assert reopened.get("job-a").state.value == "deadline_expired"
        assert reopened.get("job-b").state.value == "failed"


def test_interrupted_retry_quota_is_persisted(tmp_path: Path) -> None:
    from scryntic.jobs.store import JobStore

    with JobStore(installation(tmp_path), clock=lambda: NOW) as store:
        store.submit(job())
        for number in range(1, 4):
            active = store.claim("job-a", resume=number > 1)
            assert active.attempt == number
            assert store.finish(active, "interrupted", reason="shutdown")
        assert store.claim("job-a", resume=True).reason == "attempt_quota"
        assert store.get("job-a").state.value == "failed"


@pytest.mark.parametrize("defect", ["version", "hash", "request", "schema"])
def test_corrupt_state_is_rejected_before_recovery(tmp_path: Path, defect: str) -> None:
    import sqlite3

    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW) as store:
        store.submit(job())
    with sqlite3.connect(target.state_dir / "provider-jobs.sqlite3") as connection:
        if defect == "version":
            connection.execute("PRAGMA user_version=99")
        elif defect == "hash":
            connection.execute("UPDATE jobs SET request_sha256=?", ("b" * 64,))
        elif defect == "request":
            connection.execute("UPDATE jobs SET request=?", (b"{}",))
        else:
            connection.execute("CREATE TABLE unexpected (value TEXT)")
    with pytest.raises(ValueError):
        JobStore(target, clock=lambda: NOW)


@pytest.mark.parametrize("name", ["provider-jobs.lock", "provider-jobs.sqlite3"])
def test_symlink_state_is_rejected(tmp_path: Path, name: str) -> None:
    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    destination = tmp_path / "outside"
    destination.write_bytes(b"sentinel")
    (target.state_dir / name).symlink_to(destination)
    with pytest.raises((ValueError, OSError)):
        JobStore(target, clock=lambda: NOW)
    assert destination.read_bytes() == b"sentinel"


@pytest.mark.parametrize("deny_rollback", [False, True])
def test_sqlite_failure_poisons_owner_without_false_cancellation(
    tmp_path: Path, deny_rollback: bool
) -> None:
    import sqlite3

    from scryntic.jobs.store import JobStore

    target = installation(tmp_path)
    with JobStore(target, clock=lambda: NOW) as store:
        store.submit(job())
        active = store.claim("job-a")

        def authorize(
            action: int,
            first: str | None,
            second: str | None,
            database: str | None,
            caller: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_UPDATE or (
                deny_rollback
                and action == sqlite3.SQLITE_TRANSACTION
                and first == "ROLLBACK"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._connection.set_authorizer(authorize)
        with pytest.raises(sqlite3.DatabaseError):
            store.cancel("job-a")
        with pytest.raises(ValueError, match="unavailable"):
            store.get("job-a")
        with pytest.raises(ValueError, match="unavailable"):
            store.finish(active, "failed", reason="provider_failed")
    with JobStore(target, clock=lambda: NOW + 1) as restarted:
        assert restarted.get("job-a").state.value == "interrupted"
        assert restarted.get("job-a").artifact is None
