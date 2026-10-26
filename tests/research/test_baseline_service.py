"""Synthetic-only integration fixtures; no market inputs or host worker execution."""

import argparse
import asyncio
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

import tests.research.test_baselines as admission
from scripts.pi03_baselines import operate, write_leaf
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.jobs.contracts import JobState
from scryntic.jobs.durable import DurableAttempt
from scryntic.model_worker.launcher import CPUWorker
from scryntic.research import baseline_jobs
from scryntic.research.baseline_control import active_owner, owner_control
from scryntic.research.baseline_jobs import (
    POLICY_SHA256,
    BaselineJob,
    BaselineJobStore,
    BaselineService,
    encode_response,
)
from scryntic.research.baseline_scores import evaluate_algorithm
from scryntic.research.baselines import body_sha256, prefit_counts, utc_ns
from scryntic.research.ledger import ResearchLedger, Reservation
from tests.normalization.helpers import installation


@pytest.fixture(scope="module")
def synthetic() -> tuple[bytes, dict[str, Any]]:
    data = canonical_json_bytes(admission.AdmissionTests().input())
    result = evaluate_algorithm(data, "zero")
    result["workload_sha256"] = baseline_jobs.workload_identity()
    result["controls_sha256"] = body_sha256({})
    result["resource_usage"] = {"elapsed_ns": 1, "cpu_ns": 1, "max_rss_kib": 1}
    return data, result


def prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> Installation:
    monkeypatch.setattr(
        baseline_jobs, "EXPECTED_INPUT_SHA256", sha256(data).hexdigest()
    )
    root = installation(tmp_path)
    with ResearchLedger(root, clock=lambda: 0) as ledger:
        ledger.reserve(
            Reservation(
                POLICY_SHA256,
                utc_ns("2026-11-01T00:00:00Z"),
                utc_ns("2030-01-01T00:00:00Z"),
                utc_ns("2026-10-01T00:00:00Z"),
            )
        )
    write_leaf(root, "pi03-input.json", data)
    write_leaf(
        root, "pi03-prefit-qualified.json", canonical_json_bytes(prefit_counts(data))
    )
    return root


def test_status_does_not_register_or_consume_trial_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    root = prepared(tmp_path, monkeypatch, synthetic[0])
    report, code = asyncio.run(
        operate(argparse.Namespace(operation="status", algorithm="zero"), root)
    )
    assert code == 0
    assert report["state"] == "not_submitted"
    with ResearchLedger(root) as ledger:
        assert not [e for e in ledger.events if e["kind"] == "trial"]


def test_qualification_probe_cannot_consume_a_fitting_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    root = prepared(tmp_path, monkeypatch, synthetic[0])

    async def probe(
        worker: CPUWorker, attempt: DurableAttempt[BaselineJob], data: bytes
    ) -> bytes:
        worker.last_controls = {}
        return canonical_json_bytes(
            {
                "schema": "scryntic.pi03.worker-probe.v1",
                "job_sha256": attempt.job.sha256,
                "input_sha256": attempt.job.input_sha256,
                "model_fitted": False,
                "final_labels_accessed": False,
            }
        )

    monkeypatch.setattr(CPUWorker, "probe_baseline", probe)
    report, code = asyncio.run(
        operate(argparse.Namespace(operation="probe", algorithm=None), root)
    )
    assert code == 0
    assert report["probe"]["model_fitted"] is False
    with ResearchLedger(root) as ledger:
        assert not [e for e in ledger.events if e["kind"] == "trial"]


@pytest.mark.parametrize("verified_controls", [True, False])
def test_service_cli_result_and_failure_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
    verified_controls: bool,
) -> None:
    data, result = synthetic
    root = prepared(tmp_path, monkeypatch, data)
    calls = []

    async def execute(
        worker: CPUWorker, attempt: DurableAttempt[BaselineJob], payload: bytes
    ) -> bytes:
        calls.append(attempt.number)
        worker.last_controls = {} if verified_controls else None
        return encode_response(attempt, result)

    monkeypatch.setattr(CPUWorker, "execute_baseline", execute)

    async def run() -> tuple[str, dict[str, Any] | None]:
        with BaselineJobStore(root) as store:
            service = BaselineService(store, data)
            try:
                record = service.submit("zero")
                record = await service.run(record.job.job_id)
                assert record.state is (
                    JobState.SUCCEEDED if verified_controls else JobState.FAILED
                )
                return record.job.job_id, service.result(
                    record.job.job_id
                ) if verified_controls else None
            finally:
                await service.aclose()

    job_id, direct = asyncio.run(run())
    report, code = asyncio.run(
        operate(argparse.Namespace(operation="status", algorithm="zero"), root)
    )
    assert code == 0
    assert report["job_id"] == job_id
    assert report["result"] == direct
    assert report["reason"] == (None if verified_controls else "provider_failed")
    assert calls == [1]


def test_cancel_waits_for_actual_owner_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    data, _ = synthetic
    root = prepared(tmp_path, monkeypatch, data)

    async def scenario() -> None:
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def execute(
            worker: CPUWorker, attempt: DurableAttempt[BaselineJob], payload: bytes
        ) -> bytes:
            started.set()
            try:
                await asyncio.Event().wait()
                return b""
            finally:
                cleaned.set()

        monkeypatch.setattr(CPUWorker, "execute_baseline", execute)
        with BaselineJobStore(root) as store:
            service = BaselineService(store, data)
            try:
                record = service.submit("zero")
                task = asyncio.create_task(service.run(record.job.job_id))
                await started.wait()
                service.cancel(record.job.job_id)
                assert (await task).state is JobState.CANCELLED
                assert cleaned.is_set()
            finally:
                await service.aclose()
        with BaselineJobStore(root) as reopened:
            assert reopened.get(record.job.job_id).state is JobState.CANCELLED
        with ResearchLedger(root) as ledger:
            phases = [e["body"]["phase"] for e in ledger.events if e["kind"] == "trial"]
            assert "started" in phases
            assert "intervention" in phases
            assert "cancelled" in phases

    asyncio.run(scenario())


def test_separate_cli_status_and_cancel_reach_running_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    long_path = tmp_path / ("installation-" + "x" * 90)
    long_path.mkdir()
    root = prepared(long_path, monkeypatch, synthetic[0])

    async def scenario() -> None:
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def execute(
            worker: CPUWorker, attempt: DurableAttempt[BaselineJob], payload: bytes
        ) -> bytes:
            started.set()
            try:
                await asyncio.Event().wait()
                return b""
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        monkeypatch.setattr(CPUWorker, "execute_baseline", execute)
        task = asyncio.create_task(
            operate(argparse.Namespace(operation="run", algorithm="zero"), root)
        )

        async def cli(operation: str) -> tuple[dict[str, Any], int]:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "scripts/pi03_baselines.py",
                operation,
                "--state-dir",
                str(root.state_dir),
                "--algorithm",
                "zero",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert not stderr
            assert process.returncode is not None
            return json.loads(stdout), process.returncode

        try:
            await asyncio.wait_for(started.wait(), 5)
            status, code = await cli("status")
            assert code == 0
            assert status["state"] == "running"
            cancelled, code = await cli("cancel")
            assert code == 0
            assert cancelled["state"] == "cancelled"
            assert cleaned.is_set()
            result, code = await task
            assert code == 1
            assert result["state"] == "cancelled"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not (root.state_dir / "pi03-control.sock").exists()
        with ResearchLedger(root) as ledger:
            starts = [
                e
                for e in ledger.events
                if e["kind"] == "trial" and e["body"]["phase"] == "started"
            ]
            assert len(starts) == 1

    asyncio.run(scenario())


def test_control_rejects_fitting_commands_and_unsafe_socket_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    root = prepared(tmp_path, monkeypatch, synthetic[0])

    async def scenario() -> None:
        calls = []

        async def dispatch(
            operation: str, algorithm: str
        ) -> tuple[dict[str, Any], int]:
            calls.append((operation, algorithm))
            return {}, 0

        async with owner_control(root, dispatch):
            from scryntic.configuration.paths import directory

            with directory(root.state_dir, root.owner_uid, private=True) as fd:
                reader, writer = await asyncio.open_unix_connection(
                    path=f"/proc/self/fd/{fd}/pi03-control.sock"
                )
                writer.write(b'["run","zero"]\n')
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 2) == b""
                writer.close()
                await writer.wait_closed()
            assert calls == []
        leaf = root.state_dir / "pi03-control.sock"
        target = root.state_dir / "pi03-input.json"
        leaf.symlink_to(target)
        with pytest.raises(ValueError, match="Unsafe PI03 control socket"):
            await active_owner(root, "cancel", "zero")
        assert leaf.is_symlink()
        assert target.is_file()

    asyncio.run(scenario())


def test_recovery_counts_retry_and_drops_stale_attempt_before_artifact_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic: tuple[bytes, dict[str, Any]],
) -> None:
    data, result = synthetic
    root = prepared(tmp_path, monkeypatch, data)
    with BaselineJobStore(root) as store:
        service = BaselineService(store, data)
        record = service.submit("zero")
        abandoned = store.claim(record.job.job_id)
    old_response = encode_response(abandoned.execution(), result)

    async def scenario() -> None:
        with BaselineJobStore(root) as recovered:
            service = BaselineService(recovered, data)

            async def execute(
                worker: CPUWorker, attempt: DurableAttempt[BaselineJob], payload: bytes
            ) -> bytes:
                assert attempt.number == 2
                service._accept(abandoned, old_response)
                assert not list(root.state_dir.glob("pi03-result-*"))
                worker.last_controls = {}
                return encode_response(attempt, result)

            monkeypatch.setattr(CPUWorker, "execute_baseline", execute)
            try:
                assert recovered.get(record.job.job_id).state is JobState.INTERRUPTED
                complete = await service.run(record.job.job_id, resume=True)
                assert complete.state is JobState.SUCCEEDED
                assert complete.attempt == 2
                assert service.result(complete.job.job_id) == result
            finally:
                await service.aclose()
        with ResearchLedger(root) as ledger:
            starts = [
                e["body"]["attempt"]
                for e in ledger.events
                if e["kind"] == "trial" and e["body"]["phase"] == "started"
            ]
            assert starts == [1, 2]

    asyncio.run(scenario())
