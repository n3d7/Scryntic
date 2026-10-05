"""A stopped writer must fail admitted work without losing durable results."""

import queue
import sqlite3
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Literal

import pytest

from scryntic.ingestion import sqlite_spool as spool
from tests.ingestion.test_sqlite_spool import installation
from tests.normalization.helpers import raw_record


@pytest.mark.parametrize("error_type", [RuntimeError, SystemExit])
@pytest.mark.parametrize("phase", ["select", "dispatch", "completed"])
def test_post_startup_writer_failure_completes_admitted_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
    phase: Literal["select", "dispatch", "completed"],
) -> None:
    paused = threading.Event()
    release = threading.Event()
    original_dispatch = spool.DurableIngestor._dispatch_request

    def pause_then_fail(self: spool.DurableIngestor) -> None:
        assert self._ready.done()
        paused.set()
        assert release.wait(5), "Test did not release the writer"
        raise error_type("injected post-startup writer failure")

    def fail_selection(
        self: spool.DurableIngestor,
    ) -> tuple[queue.Queue[spool._Request], spool._Request] | None:
        pause_then_fail(self)
        raise AssertionError("Writer unexpectedly resumed")

    def fail_dispatch(
        self: spool.DurableIngestor,
        connection: sqlite3.Connection,
        lane: queue.Queue[spool._Request],
        request: spool._Request,
    ) -> bool:
        assert self._ready.done()
        paused.set()
        assert release.wait(5), "Test did not release the writer"
        if phase == "completed":
            assert original_dispatch(self, connection, lane, request)
        raise error_type("injected post-startup writer failure")

    if phase == "select":
        monkeypatch.setattr(spool.DurableIngestor, "_next_request", fail_selection)
    else:
        monkeypatch.setattr(spool.DurableIngestor, "_dispatch_request", fail_dispatch)

    target = installation(tmp_path)
    ingestor = spool.DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-a",
        capacity=4,
        max_payload_bytes=4096,
    )
    envelope = raw_record().envelope
    active = spool._AcceptRequest(envelope, Future())
    pending: tuple[spool._Request, ...] = (
        spool._StatusRequest(Future()),
        spool._ReadRequest(0, 1, Future()),
        spool._AcceptRequest(envelope, Future()),
        spool._RecoveryRequest("stream-a", b"pending", None, Future()),
    )
    try:
        if phase != "select":
            ingestor._submit(active)
        assert paused.wait(2), "Writer did not reach the post-startup fault"
        for request in pending:
            ingestor._submit(request)
        release.set()
        ingestor._thread.join(timeout=2)
        assert not ingestor._thread.is_alive(), "Writer did not terminate"

        for request in pending:
            with pytest.raises(spool.IngestionError, match="writer failed"):
                request.result.result(timeout=1)
        if phase == "dispatch":
            with pytest.raises(spool.IngestionError, match="writer failed"):
                active.result.result(timeout=1)
        elif phase == "completed":
            accepted = active.result.result(timeout=1)
            assert accepted.identity.offset == 1
            assert accepted.envelope == envelope

        # Check admission first so even a broken implementation cannot hang the test.
        status_request = spool._StatusRequest(Future())
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor._submit(status_request)
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor.status()
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor.accept(envelope)
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor.records_after(0, limit=1)
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor.recovery_get("stream-a")
        with pytest.raises(spool.IngestionError, match="writer failed"):
            ingestor.recovery_put("stream-a", b"new", expected=None)
    finally:
        release.set()
        ingestor.close()
        monkeypatch.undo()

    with spool.DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=4,
        max_payload_bytes=4096,
    ) as reopened:
        assert reopened.status().accepted_offset == (1 if phase == "completed" else 0)
        assert reopened.recovery_get("stream-a") is None
        records = reopened.records_after(0, limit=2)
        assert len(records) == (1 if phase == "completed" else 0)
        if records:
            assert records[0].envelope == envelope
