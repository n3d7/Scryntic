"""Prepare or execute the single registered PI03 development workload."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.jobs.contracts import JobState
from scryntic.jobs.durable import DurableAttempt, DurableRecord
from scryntic.research.baseline_control import active_owner, owner_control
from scryntic.research.baseline_jobs import (
    POLICY_SHA256,
    BaselineArtifact,
    BaselineJob,
    BaselineJobStore,
    BaselineService,
)
from scryntic.research.baseline_scores import compare_results
from scryntic.research.baselines import (
    ALGORITHMS,
    REQUEST_BYTES,
    load_spec,
    prefit_counts,
    prepare_input,
    utc_ns,
)
from scryntic.research.ledger import ResearchLedger, Reservation


def installation(path: Path) -> Installation:
    path = path.absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    uid = os.geteuid()
    return Installation(
        config_dir=path,
        state_dir=path,
        runtime_dir=path,
        credential_dir=path,
        owner_uid=uid,
        config_uid=uid,
        service_mode=False,
    )


def read_leaf(root: Installation, name: str, maximum: int) -> bytes:
    with directory(root.state_dir, root.owner_uid, private=True) as parent:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != root.owner_uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= maximum
            ):
                raise ValueError("Unsafe PI03 prepared input")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(maximum + 1)
        finally:
            os.close(fd)


def write_leaf(root: Installation, name: str, data: bytes) -> None:
    try:
        existing = read_leaf(root, name, len(data))
    except FileNotFoundError:
        pass
    else:
        if existing != data:
            raise ValueError("Immutable PI03 prepared object differs")
        return
    with directory(root.state_dir, root.owner_uid, private=True) as parent:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            os.fsync(parent)
        finally:
            os.close(fd)


def prepare(args: argparse.Namespace, root: Installation) -> dict[str, Any]:
    spec = load_spec(args.spec)
    with ResearchLedger(root) as ledger:
        ledger.reserve(
            Reservation(
                POLICY_SHA256,
                utc_ns("2026-11-01T00:00:00Z"),
                utc_ns("2030-01-01T00:00:00Z"),
                utc_ns("2026-10-01T00:00:00Z"),
            )
        )
        data = prepare_input(
            args.policy, args.pi02_spec, args.spec, args.archive_root, ledger
        )
    report = prefit_counts(data)
    for horizon, expected in spec["folds"][0][
        "expected_complete_origins_per_asset"
    ].items():
        if report["horizons"][horizon]["paired_origins_per_asset"] != expected:
            raise ValueError("Registered source has unexpected PI03 exclusions")
    write_leaf(root, "pi03-input.json", data)
    write_leaf(root, "pi03-prefit-qualified.json", canonical_json_bytes(report))
    return report


async def operate(
    args: argparse.Namespace, root: Installation
) -> tuple[dict[str, Any], int]:
    if args.operation in ("status", "cancel") and args.algorithm is not None:
        reply = await active_owner(root, args.operation, args.algorithm)
        if reply is not None:
            return reply
    data = read_leaf(root, "pi03-input.json", REQUEST_BYTES)
    if canonical_json_bytes(prefit_counts(data)) != read_leaf(
        root, "pi03-prefit-qualified.json", 65536
    ):
        raise ValueError("PI03 pre-fit input/count registration changed")
    with BaselineJobStore(root) as store:
        service = BaselineService(store, data)
        try:
            if args.operation == "probe":
                now = store.now()
                job = BaselineJob("zero", service.data_sha256, now, now + 180 * 10**9)
                probe = await service.executor.worker.probe_baseline(
                    DurableAttempt(job, 1, uuid4().hex), data
                )
                return {
                    "schema": "scryntic.pi03.cli-probe.v1",
                    "probe": json.loads(probe),
                    "controls": service.executor.worker.last_controls,
                    "usage": service.executor.worker.last_usage,
                }, 0
            if args.operation == "compare":
                return _comparison(service), 0
            if args.algorithm is None:
                raise ValueError("A registered algorithm is required")
            record = (
                service.submit(args.algorithm)
                if args.operation == "run"
                else service.find(args.algorithm)
            )
            if record is None:
                return _report(service, None, args.operation)
            if args.operation == "run":
                record = await _run_owned(service, store, root, record)
            elif args.operation == "cancel":
                record = service.cancel(record.job.job_id)
            return _report(service, record, args.operation)
        finally:
            await service.aclose()


def _comparison(service: BaselineService) -> dict[str, Any]:
    results = {}
    for algorithm in ALGORITHMS:
        existing = service.find(algorithm)
        if existing is None:
            raise ValueError("Registered PI03 comparison is incomplete")
        results[algorithm] = service.result(existing.job.job_id)
    return compare_results(results, service.input["spec"])


async def _run_owned(
    service: BaselineService,
    store: BaselineJobStore,
    root: Installation,
    record: DurableRecord[BaselineJob, BaselineArtifact],
) -> DurableRecord[BaselineJob, BaselineArtifact]:
    async def dispatch(operation: str, algorithm: str) -> tuple[dict[str, Any], int]:
        existing = service.find(algorithm)
        if existing is None:
            return _report(service, None, operation)
        if operation == "cancel":
            await service.cancel_and_wait(existing.job.job_id)
        return _report(service, store.get(existing.job.job_id), operation)

    async with owner_control(root, dispatch):
        return await service.run(
            record.job.job_id, resume=record.state is JobState.INTERRUPTED
        )


def _report(
    service: BaselineService,
    record: DurableRecord[BaselineJob, BaselineArtifact] | None,
    operation: str,
) -> tuple[dict[str, Any], int]:
    if record is None:
        return {
            "schema": "scryntic.pi03.cli-result.v1",
            "state": "not_submitted",
            "reason": None,
            "result": None,
            "artifact_sha256": None,
        }, 0
    result = (
        service.result(record.job.job_id)
        if record.state is JobState.SUCCEEDED
        else None
    )
    return {
        "schema": "scryntic.pi03.cli-result.v1",
        "job_id": record.job.job_id,
        "state": record.state.value,
        "attempt": record.attempt,
        "reason": record.reason,
        "artifact_sha256": record.artifact.sha256 if record.artifact else None,
        "result": result,
        "controls": service.executor.worker.last_controls,
        "usage": service.executor.worker.last_usage,
    }, 0 if result is not None or operation != "run" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("prepare", "probe", "run", "status", "cancel", "compare")
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--algorithm", choices=ALGORITHMS)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("docs/architecture/PI00_RESEARCH_POLICY_v2.json"),
    )
    parser.add_argument(
        "--pi02-spec",
        type=Path,
        default=Path("docs/architecture/PI02_FEASIBILITY_SPEC_v1.json"),
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path("docs/architecture/PI03_BASELINE_SPEC_v1.json"),
    )
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.operation == "prepare" and args.archive_root is None:
        parser.error("prepare requires the retained --archive-root")
    try:
        root = installation(args.state_dir)
        report, code = (
            (prepare(args, root), 0)
            if args.operation == "prepare"
            else asyncio.run(operate(args, root))
        )
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError):
        report, code = (
            {
                "schema": "scryntic.pi03.cli-failure.v1",
                "reason": "pi03_admission_or_execution_failed",
            },
            1,
        )
    payload = canonical_json_bytes(report)
    if args.output is not None:
        fd = os.open(
            args.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    else:
        print(payload.decode())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
