"""Compare locally approved raw codecs in fresh, bounded Linux processes.

Run with the project Python: .venv/bin/python scripts/benchmark_raw.py --help.
Durable seal includes codec validation, immutable installation and fsync. A
verified full scan decodes once; public point reads retain the real RawArchive
contract and can decode the whole segment each time. No timing assertion or
production backpressure/recovery/pruning policy is introduced here.
"""

import argparse
import asyncio
import importlib.metadata
import json
import multiprocessing
import os
import platform
import random
import resource
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from hashlib import sha256
from multiprocessing.connection import Connection
from pathlib import Path

from scryntic.application.archive import ArchiveLimits, RawRecordRef
from scryntic.archive.raw_framed import FramedZlibRawArchive
from scryntic.archive.raw_parquet import ArchiveError, ParquetRawArchive
from scryntic.benchmarks.workloads import (
    WorkloadSpec,
    build_workload,
    recorded_fixture_path,
    workload_digest,
)
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import RawRecord
from scryntic.ingestion.sqlite_spool import DurableIngestor


def _archive(
    codec: str, installation: Installation
) -> ParquetRawArchive | FramedZlibRawArchive:
    if codec == "parquet":
        return ParquetRawArchive(installation)
    if codec == "framed-zlib":
        return FramedZlibRawArchive(installation)
    raise ValueError("Unknown locally approved benchmark codec")


def _installation(root: Path) -> Installation:
    paths = tuple(root / name for name in ("config", "state", "runtime", "credentials"))
    for path in paths:
        path.mkdir(mode=0o700)
    return Installation(
        paths[0], paths[1], paths[2], paths[3], os.geteuid(), os.geteuid(), False
    )


def _rss_bytes() -> int:
    # Linux ru_maxrss is KiB. Environment records the platform explicitly.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _read_loop(
    codec: str,
    installation: Installation,
    path: Path,
    digest: str,
    limits: ArchiveLimits,
    ready: Connection,
) -> None:
    archive = _archive(codec, installation)
    archive._read_all(path, digest, limits)
    ready.send("verified; entering repeated reads")
    ready.close()
    while True:
        archive._read_all(path, digest, limits)


def _interruption(
    codec: str,
    installation: Installation,
    path: Path,
    digest: str,
    limits: ArchiveLimits,
) -> dict[str, object]:
    # Read-only worker interruption is a measurement, not F11 crash recovery.
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_read_loop, args=(codec, installation, path, digest, limits, sender)
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(30):
            raise RuntimeError("Read-interruption worker timed out")
        receiver.recv()
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        healthy = len(_archive(codec, installation)._read_all(path, digest, limits)) > 0
        return {
            "method": "SIGTERM repeated full-read worker after one verified read",
            "worker_exitcode": process.exitcode,
            "healthy_reread": healthy,
            "limit": "Signal position within a read is scheduler-dependent; no crash-recovery claim",
        }
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
            process.join(5)


def _truncated_reads(
    archive: ParquetRawArchive | FramedZlibRawArchive,
    path: Path,
    digest: str,
    limits: ArchiveLimits,
    root: Path,
) -> dict[str, bool]:
    content = path.read_bytes()
    outcomes = {}
    for label, size in (
        ("header", min(3, len(content) - 1)),
        ("middle", len(content) // 2),
        ("tail", len(content) - 8),
    ):
        truncated = content[: max(0, size)]
        corrupt = root / f"truncated-{label}"
        corrupt.write_bytes(truncated)
        corrupt.chmod(0o400)
        for hash_label, expected in (
            ("original_hash", digest),
            ("rehashed", sha256(truncated).hexdigest()),
        ):
            rejected = False
            try:
                archive._read_all(corrupt, expected, limits)
            except ArchiveError:
                rejected = True
            outcomes[f"{label}_{hash_label}_rejected"] = rejected
    return outcomes


def _point_summary(durations: list[float]) -> dict[str, object]:
    ordered = sorted(durations)
    return {
        "count": len(durations),
        "seconds": sum(durations),
        "median_seconds": statistics.median(durations),
        "p95_seconds": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max_seconds": max(durations),
    }


def _spool_sizes(installation: Installation) -> dict[str, int]:
    files = {
        "sqlite": installation.state_dir / "ingestion.sqlite3",
        "wal": installation.state_dir / "ingestion.sqlite3-wal",
        "shm": installation.state_dir / "ingestion.sqlite3-shm",
    }
    return {
        name: path.stat().st_size if path.exists() else 0
        for name, path in files.items()
    }


def _measure_ingestion(
    spec: WorkloadSpec,
    installation: Installation,
    segments: int,
    repeat: int,
    sample_limit: int,
) -> dict[str, object]:
    """F05 serialized committed intake; capacity is not a policy recommendation."""
    committed = verified = 0
    write_seconds = read_seconds = 0.0
    latencies = []
    with DurableIngestor(
        installation,
        producer="benchmark-spool",
        epoch="benchmark-epoch",
        capacity=64,
        max_payload_bytes=spec.payload_limit,
    ) as spool:
        before = _spool_sizes(installation)
        for segment_index in range(segments):
            records = build_workload(spec, segment=repeat * segments + segment_index)
            for record in records[: sample_limit - committed]:
                started = time.perf_counter()
                accepted = spool.accept(record.envelope)
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)
                write_seconds += elapsed
                committed += 1
                if accepted.envelope != record.envelope:
                    raise RuntimeError("Durable ingestion changed raw envelope")
            if committed == sample_limit:
                break
        during = _spool_sizes(installation)
        offset = 0
        for segment_index in range(segments):
            expected = build_workload(spec, segment=repeat * segments + segment_index)
            expected = expected[: sample_limit - verified]
            started = time.perf_counter()
            records = spool.records_after(offset, limit=spec.records)
            read_seconds += time.perf_counter() - started
            if tuple(record.envelope for record in records) != tuple(
                record.envelope for record in expected
            ):
                raise RuntimeError("F05 records_after changed raw envelope order")
            offset = records[-1].identity.offset
            verified += len(records)
            if verified == sample_limit:
                break
        status = asdict(spool.status())
    after = _spool_sizes(installation)
    return {
        "mode": "sequential synchronous accept; committed readback before acknowledgement; no batching",
        "intake_capacity": 64,
        "sample_limit": sample_limit,
        "sample_method": "first sample_limit envelopes of independently generated segments; raw codec benchmark uses every segment",
        "committed_records": committed,
        "verified_records_after": verified,
        "commit_seconds": write_seconds,
        "committed_records_per_second": committed / write_seconds,
        "commit_latency": _point_summary(latencies),
        "records_after_seconds": read_seconds,
        "records_after_records_per_second": verified / read_seconds,
        "bytes_before": before,
        "bytes_while_open": during,
        "bytes_after_close": after,
        "sqlite_bytes_after_close": after["sqlite"],
        "status": status,
        "limit": "Opaque raw events only; excludes F06 normalization, F08/F09 publication and production queue policy",
    }


async def _verify_point_reads(
    archive: ParquetRawArchive | FramedZlibRawArchive,
    digest: str,
    limits: ArchiveLimits,
    records: tuple[RawRecord, ...],
    points: int,
    rng: random.Random,
    sequential: list[float],
    random_reads: list[float],
) -> None:
    for indices, timings in (
        (range(min(points, len(records))), sequential),
        (rng.sample(range(len(records)), min(points, len(records))), random_reads),
    ):
        for index in indices:
            started = time.perf_counter()
            actual = await archive.read(
                RawRecordRef(digest, index, records[index].identity), limits
            )
            timings.append(time.perf_counter() - started)
            if actual != records[index]:
                raise RuntimeError("Public point read changed raw bytes or identity")


async def _measure(
    spec: WorkloadSpec,
    codec: str,
    segments: int,
    points: int,
    root: Path,
    repeat: int,
    measure_ingestion: bool = True,
    ingestion_records: int = 2048,
) -> dict[str, object]:
    installation = _installation(root)
    archive = _archive(codec, installation)
    baseline_rss = _rss_bytes()
    limits = ArchiveLimits(spec.records, 268_435_456, 268_435_456)
    write_seconds = scan_seconds = 0.0
    payload_bytes = encoded_bytes = logical_bytes = record_count = 0
    generation_seconds = 0.0
    sequential: list[float] = []
    random_reads: list[float] = []
    hashes: list[str] = []
    digests: list[str] = []
    rng = random.Random(spec.seed)
    case_started = time.perf_counter()
    for segment_index in range(segments):
        started = time.perf_counter()
        records = build_workload(spec, segment=repeat * segments + segment_index)
        generation_seconds += time.perf_counter() - started
        digests.append(workload_digest(records))
        payload_bytes += sum(len(record.envelope.payload) for record in records)
        if payload_bytes > 536_870_912:
            raise ValueError("Case exceeds 512 MiB total payload budget")
        record_count += len(records)
        started = time.perf_counter()
        segment = await archive.seal(records, limits)
        write_seconds += time.perf_counter() - started
        encoded_bytes += segment.encoded_bytes
        logical_bytes += segment.decoded_bytes
        hashes.append(segment.sha256)
        path = archive.object_path(segment.sha256)
        started = time.perf_counter()
        decoded = archive._read_all(path, segment.sha256, limits)
        scan_seconds += time.perf_counter() - started
        if decoded != records:
            raise RuntimeError("Verified scan changed raw bytes or identity")
        if segment_index not in {0, segments - 1}:
            continue
        await _verify_point_reads(
            archive,
            segment.sha256,
            limits,
            records,
            points,
            rng,
            sequential,
            random_reads,
        )
    # Codec peak includes generated input, sealing, scan and point reads only.
    peak_rss = _rss_bytes()
    measured_wall_seconds = time.perf_counter() - case_started
    truncated = _truncated_reads(archive, path, segment.sha256, limits, root)
    if not all(truncated.values()):
        raise RuntimeError("A truncated raw archive was accepted")
    interrupted = _interruption(codec, installation, path, segment.sha256, limits)
    if not interrupted["healthy_reread"]:
        raise RuntimeError("Healthy reread failed after read-worker interruption")
    ingestion = (
        _measure_ingestion(
            spec,
            installation,
            segments,
            repeat,
            min(ingestion_records, spec.records * segments),
        )
        if measure_ingestion
        else None
    )
    fixture_evidence = None
    if spec.kind == "recorded-candle":
        fixture_path = recorded_fixture_path(spec)
        fixture_bytes = fixture_path.read_bytes()
        fixture_evidence = {
            "path": str(fixture_path),
            "sha256": sha256(fixture_bytes).hexdigest(),
            "provenance": json.loads(fixture_bytes)["provenance"],
            "replay": "exact fixed public HTTP response payloads; synthetic receipts; source_time unparsed; no live chronology claim",
        }
    return {
        "codec": segment.codec,
        "workload": asdict(spec),
        "repeat": repeat,
        "workload_digest": sha256("".join(digests).encode()).hexdigest(),
        "segment_workload_digests": digests,
        "segment_hashes": hashes,
        "segment_count": segments,
        "record_count": record_count,
        "generation_seconds": generation_seconds,
        "measured_wall_seconds": measured_wall_seconds,
        "point_read_sampling": "first and last segment only; ordered prefix and seeded random indices",
        "recorded_fixture": fixture_evidence,
        "payload_bytes": payload_bytes,
        "logical_decoded_bytes": logical_bytes,
        "encoded_bytes": encoded_bytes,
        "encoded_to_payload_ratio": encoded_bytes / payload_bytes,
        "durable_write_seconds": write_seconds,
        "durable_write_records_per_second": record_count / write_seconds,
        "durable_write_payload_bytes_per_second": payload_bytes / write_seconds,
        "verified_scan_seconds": scan_seconds,
        "verified_scan_records_per_second": record_count / scan_seconds,
        "verified_scan_payload_bytes_per_second": payload_bytes / scan_seconds,
        "public_sequential_point_reads": _point_summary(sequential),
        "public_random_point_reads": _point_summary(random_reads),
        "baseline_process_rss_bytes": baseline_rss,
        "peak_process_rss_bytes": peak_rss,
        "peak_process_rss_with_ingestion_bytes": _rss_bytes(),
        "ingestion": ingestion,
        "truncated_reads": truncated,
        "interruption": interrupted,
        "archive_limits": asdict(limits),
    }


def _environment(root: Path) -> dict[str, object]:
    resolved = root.resolve()
    mounts = []
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4 and resolved.is_relative_to(Path(fields[1])):
            mounts.append((len(fields[1]), fields))
    mount = max(mounts)[1] if mounts else []
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        platform.processor(),
    )
    fs = os.statvfs(resolved)
    repository = Path(__file__).resolve().parents[1]
    source_paths = (
        "scripts/benchmark_raw.py",
        "src/scryntic/benchmarks/workloads.py",
        "src/scryntic/archive/raw_parquet.py",
        "src/scryntic/archive/raw_framed.py",
        "src/scryntic/archive/canonical.py",
        "src/scryntic/archive/storage.py",
        "src/scryntic/ingestion/sqlite_spool.py",
        "pyproject.toml",
        "uv.lock",
    )
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": cpu,
        "cpu_count": os.cpu_count(),
        "physical_memory_bytes": os.sysconf("SC_PAGE_SIZE")
        * os.sysconf("SC_PHYS_PAGES"),
        "pyarrow": importlib.metadata.version("pyarrow"),
        "zlib_runtime": __import__("zlib").ZLIB_RUNTIME_VERSION,
        "storage_root": str(resolved),
        "filesystem_mount": mount,
        "filesystem_block_bytes": fs.f_bsize,
        "filesystem_available_bytes": fs.f_bavail * fs.f_frsize,
        "cache_policy": "warm OS cache; no privileged cache drop; fresh process per case/repeat",
        "rss_policy": "Linux process ru_maxrss including Python, dependency imports, generated inputs, codec and read verification",
        "provenance": "committed public candles replayed exactly; seeded synthetic trade/orderbook shapes",
        "source_file_sha256": {
            relative: sha256((repository / relative).read_bytes()).hexdigest()
            for relative in source_paths
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["candle", "recorded-candle", "trade", "orderbook", "mixed"],
        default=["recorded-candle", "trade", "orderbook"],
    )
    parser.add_argument("--instruments", type=int, nargs="+", default=[5, 20])
    parser.add_argument("--records", type=int, default=512)
    parser.add_argument("--seed", type=int, default=14)
    parser.add_argument("--levels", type=int, default=100)
    parser.add_argument("--max-trade-batch", type=int, default=40)
    parser.add_argument(
        "--candle-fixture",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks/fixtures/candles.json",
    )
    parser.add_argument("--segments", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--point-reads", type=int, default=16)
    parser.add_argument(
        "--ingestion-records",
        type=int,
        default=None,
        help="F05 committed sample count (default min(2048, total case records), at most 100000)",
    )
    parser.add_argument(
        "--codecs",
        nargs="+",
        choices=["parquet", "framed-zlib"],
        default=["parquet", "framed-zlib"],
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Hard subprocess timeout per case/repeat (seconds)",
    )
    parser.add_argument(
        "--storage-root", type=Path, default=Path(tempfile.gettempdir())
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repeat-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--skip-ingestion", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run_case(command: list[str], storage_root: Path, timeout: int) -> str:
    # The orchestrator owns cleanup even when a worker is killed at timeout.
    with tempfile.TemporaryDirectory(
        prefix="scryntic-bench-case-", dir=storage_root
    ) as temporary:
        with subprocess.Popen(
            [*command, "--storage-root", temporary],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as worker:
            try:
                stdout, stderr = worker.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.communicate()
                raise RuntimeError(
                    f"Benchmark case exceeded {timeout}s timeout"
                ) from None
            finally:
                # KeyboardInterrupt must not leave a separately grouped worker.
                if worker.poll() is None:
                    os.killpg(worker.pid, signal.SIGKILL)
                    worker.communicate()
            if worker.returncode:
                raise RuntimeError(
                    f"Benchmark worker failed ({worker.returncode}): {stderr}"
                )
            return stdout


def _case_command(
    args: argparse.Namespace,
    spec: WorkloadSpec,
    codec: str,
    repeat: int,
    ingestion_records: int,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--workloads",
        spec.kind,
        "--instruments",
        str(spec.instruments),
        "--records",
        str(spec.records),
        "--seed",
        str(spec.seed),
        "--levels",
        str(spec.levels),
        "--max-trade-batch",
        str(spec.max_trade_batch),
        "--candle-fixture",
        str(args.candle_fixture),
        "--segments",
        str(args.segments),
        "--point-reads",
        str(args.point_reads),
        "--ingestion-records",
        str(ingestion_records),
        "--codecs",
        codec,
        "--repeat-index",
        str(repeat),
        "--storage-root",
        str(args.storage_root),
    ]
    if codec != args.codecs[0]:
        command.append("--skip-ingestion")
    return command


def _workload_specs(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> list[WorkloadSpec]:
    specs = []
    try:
        for workload in args.workloads:
            for instruments in args.instruments:
                specs.append(
                    WorkloadSpec(
                        kind=workload,
                        instruments=instruments,
                        records=args.records,
                        seed=args.seed,
                        levels=args.levels,
                        max_trade_batch=args.max_trade_batch,
                        recorded_fixture_path=str(args.candle_fixture)
                        if workload == "recorded-candle"
                        else None,
                    )
                )
    except ValueError as error:
        parser.error(str(error))
    return specs


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error(
            "This measurement runner requires Linux ru_maxrss/proc/fork semantics"
        )
    if (
        not 1 <= args.segments <= 256
        or not 1 <= args.repeats <= 10
        or not 1 <= args.point_reads <= 1024
        or not 1 <= args.timeout <= 3600
    ):
        parser.error("segments/repeats/point reads/timeout outside benchmark bounds")
    specs = _workload_specs(args, parser)
    if len(specs) * len(args.codecs) * args.repeats > 160:
        parser.error("At most 160 isolated cases per invocation")
    total_records = args.records * args.segments
    ingestion_records = (
        min(2048, total_records)
        if args.ingestion_records is None
        else args.ingestion_records
    )
    if not 1 <= ingestion_records <= min(100_000, total_records):
        parser.error(
            "ingestion records must be positive and within case records/100000 cap"
        )
    if args.worker:
        with tempfile.TemporaryDirectory(
            prefix="scryntic-raw-bench-", dir=args.storage_root
        ) as temporary:
            print(
                json.dumps(
                    asyncio.run(
                        _measure(
                            specs[0],
                            args.codecs[0],
                            args.segments,
                            args.point_reads,
                            Path(temporary),
                            args.repeat_index,
                            not args.skip_ingestion,
                            ingestion_records,
                        )
                    )
                )
            )
        return
    if args.output is None:
        parser.error("--output is required")
    environment = _environment(args.storage_root)
    results = []
    for spec in specs:
        for codec in args.codecs:
            for repeat in range(args.repeats):
                command = _case_command(args, spec, codec, repeat, ingestion_records)
                results.append(
                    json.loads(_run_case(command, args.storage_root, args.timeout))
                )
                print(
                    f"{spec.kind}/{spec.instruments}/{codec}/repeat-{repeat}: verified",
                    file=sys.stderr,
                )
    report = {
        "schema": "scryntic.raw-benchmark.v1",
        "command": [sys.executable, *sys.argv],
        "environment": environment,
        "parameters": {
            "segments": args.segments,
            "repeats": args.repeats,
            "point_reads": args.point_reads,
            "ingestion_records": ingestion_records,
            "timeout_seconds": args.timeout,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
