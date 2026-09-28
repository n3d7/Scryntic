"""Quick real subprocess coverage; elapsed values are evidence, not assertions."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path


def test_benchmark_cli_reports_durable_roundtrips_and_fault_observations(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_raw.py",
            "--workloads",
            "mixed",
            "--instruments",
            "5",
            "--records",
            "15",
            "--segments",
            "2",
            "--repeats",
            "1",
            "--point-reads",
            "2",
            "--levels",
            "4",
            "--ingestion-records",
            "10",
            "--codecs",
            "parquet",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report["schema"] == "scryntic.raw-benchmark.v1"
    assert report["environment"]["python"]
    result = report["results"][0]
    assert result["record_count"] == 30
    assert result["segment_count"] == 2
    assert len(set(result["segment_hashes"])) == 2
    assert result["peak_process_rss_bytes"] > 0
    assert result["encoded_bytes"] > 0
    assert result["durable_write_seconds"] > 0
    assert result["verified_scan_seconds"] > 0
    assert result["public_sequential_point_reads"]["count"] == 4
    assert result["public_random_point_reads"]["count"] == 4
    assert all(result["truncated_reads"].values())
    assert result["interruption"]["healthy_reread"] is True
    assert result["workload_digest"]
    assert result["ingestion"]["committed_records"] == 10
    assert result["ingestion"]["committed_records_per_second"] > 0
    assert result["ingestion"]["sqlite_bytes_after_close"] > 0
    assert result["ingestion"]["verified_records_after"] == 10
    assert result["ingestion"]["sample_limit"] == 10


def test_benchmark_cli_rejects_unbounded_workload_without_creating_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_raw.py",
            "--records",
            "100001",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert not output.exists()


def test_benchmark_timeout_cleans_case_artifacts(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_raw.py",
            "--workloads",
            "trade",
            "--instruments",
            "5",
            "--records",
            "100000",
            "--segments",
            "2",
            "--repeats",
            "1",
            "--codecs",
            "parquet",
            "--timeout",
            "1",
            "--storage-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "result.json"),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode != 0
    assert not list(tmp_path.iterdir())


def test_benchmark_cancellation_stops_worker_and_cleans_case_artifacts(
    tmp_path: Path,
) -> None:
    with subprocess.Popen(
        [
            sys.executable,
            "scripts/benchmark_raw.py",
            "--workloads",
            "trade",
            "--instruments",
            "5",
            "--records",
            "100000",
            "--segments",
            "2",
            "--repeats",
            "1",
            "--codecs",
            "parquet",
            "--storage-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "result.json"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        deadline = time.monotonic() + 5
        while not list(
            tmp_path.glob("scryntic-bench-case-*/scryntic-raw-bench-*/config")
        ):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("Benchmark worker did not start")
            time.sleep(0.02)
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=10)
        assert process.returncode != 0
    assert not list(tmp_path.iterdir())
