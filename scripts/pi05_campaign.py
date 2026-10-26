"""Finite Debian operator campaign with durable attempts and fresh cgroups."""

import argparse
import hashlib
import json
import os
import platform
import pwd
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from scryntic.analytics.decision import summarize
from scryntic.analytics.provenance import (
    file_hash,
    fixture_identity,
    runtime_identity,
    source_identity,
)


def systemctl(*arguments: str) -> dict[str, str]:
    value = subprocess.run(
        ["/usr/bin/systemctl", *arguments],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in value.splitlines() if "=" in line)


def durable_json(path: Path, value: dict[str, Any], *, replace: bool = False) -> None:
    user = pwd.getpwnam("scryntic")
    destination = path.with_name(path.name + ".new") if replace else path
    with destination.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(destination, user.pw_uid, user.pw_gid)
    destination.chmod(0o600)
    if replace:
        os.replace(destination, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sample(
    source: Path, fixture: Path, report: Path, candidate: str, sequence: int
) -> dict[str, Any]:
    unit = f"scryntic-pi05-{os.getpid()}-{sequence}-{uuid4().hex[:8]}"
    properties = (
        "Type=oneshot",
        "RemainAfterExit=yes",
        "User=scryntic",
        "Group=scryntic",
        "NoNewPrivileges=yes",
        "PrivateNetwork=yes",
        "ProtectHome=yes",
        "ProtectSystem=strict",
        f"ReadWritePaths={report.parent}",
        "MemoryAccounting=yes",
        "IOAccounting=yes",
        "CPUAccounting=yes",
        "MemoryMax=3221225472",
        "MemorySwapMax=0",
        "TasksMax=16",
        "CPUQuota=100%",
        "RuntimeMaxSec=180",
        "TimeoutStartSec=180",
        "TimeoutStopSec=2",
        "KillMode=control-group",
        f"StandardOutput=file:{report}.stdout",
        f"StandardError=file:{report}.stderr",
    )
    command = ["/usr/bin/systemd-run", "--quiet", "--no-block", f"--unit={unit}"]
    command.extend(f"--property={value}" for value in properties)
    command.extend(
        [
            "--setenv=PYTHONDONTWRITEBYTECODE=1",
            str(Path(sys.executable)),
            str(source / "scripts/pi05_scans.py"),
            "run",
            str(fixture),
            "--candidate",
            candidate,
            "--report",
            str(report),
        ]
    )
    started = time.perf_counter_ns()
    result: dict[str, Any] = {
        "candidate": candidate,
        "status": "unqualified",
        "reason": "controller-not-completed",
        "unit": unit,
    }
    try:
        subprocess.run(command, check=True, timeout=15, capture_output=True)
        deadline = time.monotonic() + 195
        while time.monotonic() < deadline:
            state = systemctl(
                "show",
                unit,
                "--no-pager",
                "--property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic,MemoryPeak,IOReadBytes,IOWriteBytes,CPUUsageNSec,ControlGroup,MainPID",
            )
            if state.get("SubState") in ("exited", "failed", "dead"):
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("PI05 outer deadline")
        if report.exists():
            result.update(json.loads(report.read_bytes()))
            if result.get("status") == "qualified":
                result.pop("reason", None)
        result.update(
            manager_exit=state.get("ExecMainStatus"),
            manager_state=state.get("SubState"),
            manager_result=state.get("Result"),
        )
        for key, field in (
            ("MemoryPeak", "cgroup_memory_peak_bytes"),
            ("IOReadBytes", "cgroup_io_read_bytes"),
            ("IOWriteBytes", "cgroup_io_write_bytes"),
            ("CPUUsageNSec", "full_job_cpu_ns"),
        ):
            value = state.get(key, "")
            if not value.isdigit():
                result.update(status="unqualified", reason="missing-cgroup-accounting")
            else:
                result[field] = int(value)
        begin = int(state.get("ExecMainStartTimestampMonotonic", "0"))
        end = int(state.get("ExecMainExitTimestampMonotonic", "0"))
        if end <= begin or begin <= 0:
            result.update(status="unqualified", reason="missing-service-duration")
        else:
            result["full_job_wall_ns"] = (end - begin) * 1000
            result["wall_scope"] = (
                "systemd service process start through exit, including interpreter/worker/IPC/verification/report fsync; excludes controller polling/setup"
            )
        group = state.get("ControlGroup", "")
        procs = Path("/sys/fs/cgroup" + group) / "cgroup.procs"
        # Debian removes empty oneshot cgroups while retaining accounting.
        empty = (
            not group
            or (procs.exists() and not procs.read_text().strip())
            or not procs.parent.exists()
        )
        result["cgroup_empty_before_stop"] = empty
        if not empty or state.get("MainPID") != "0":
            result.update(status="unqualified", reason="worker-not-reaped")
        if (
            state.get("Result") != "success"
            or state.get("ExecMainCode") != "1"
            or state.get("ExecMainStatus") != "0"
        ):
            result.update(status="unqualified", reason="outer-job-failed")
    except Exception as exc:
        result.update(
            status="unqualified",
            reason="controller-exception",
            controller_exception=type(exc).__name__,
        )
    finally:
        cleanup = []
        for action in ("stop", "reset-failed"):
            try:
                completed = subprocess.run(
                    ["/usr/bin/systemctl", action, unit],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                cleanup.append({"operation": action, "exit_code": completed.returncode})
                if action == "stop" and completed.returncode != 0:
                    result.update(status="unqualified", reason="cleanup-failed")
            except Exception as exc:
                cleanup.append({"operation": action, "exception": type(exc).__name__})
                result.update(status="unqualified", reason="cleanup-failed")
        result["controller_cleanup"] = cleanup
        result["controller_wall_ns"] = time.perf_counter_ns() - started
        try:
            user = pwd.getpwnam("scryntic")
            for leaf in (
                report,
                Path(str(report) + ".stdout"),
                Path(str(report) + ".stderr"),
            ):
                if leaf.exists():
                    os.chown(leaf, user.pw_uid, user.pw_gid)
                    leaf.chmod(0o600)
        except Exception as exc:
            result.update(
                status="unqualified",
                reason="metadata-cleanup-failed",
                metadata_cleanup_exception=type(exc).__name__,
            )
    return result


def bind_result(intent: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    result = intent | actual
    result["admitted_fixture_sha256"] = intent["fixture_sha256"]
    if (
        actual.get("status") == "qualified"
        and actual.get("fixture_sha256") != intent["fixture_sha256"]
    ):
        result.update(status="unqualified", reason="fixture-changed-after-admission")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or "ID=debian" not in Path("/etc/os-release").read_text():
        parser.error("Explicit Debian operator execution required")
    source = Path(__file__).resolve().parents[1]
    receipt = json.loads((source.parent / "PI05_TRANSFER_RECEIPT.json").read_bytes())
    if (
        receipt.get("archive_sha256") != args.source_sha256
        or receipt.get("files_verified") is not True
    ):
        parser.error("Verified transfer receipt does not match source")
    protocol_data = (source / "docs/validation/pi05/protocol-v1.json").read_bytes()
    protocol_sha256 = hashlib.sha256(protocol_data).hexdigest()
    if (
        protocol_sha256
        != "412895fb9c19a90f2cfdfe6d57f487addfd0bc613dff611b69299040f3694b4b"
    ):
        parser.error("PI05 protocol identity changed")
    protocol = json.loads(protocol_data)
    boot_file = Path("/proc/sys/kernel/random/boot_id")
    boot_id = boot_file.read_text().strip()
    identities = runtime_identity() | {
        "source_archive_sha256": args.source_sha256,
        "source_tree_sha256": source_identity(source),
        "dependency_lock_sha256": file_hash(source / "uv.lock"),
        "protocol_sha256": protocol_sha256,
        "boot_id": boot_id,
    }
    fixtures = {
        w["id"]: fixture_identity(args.fixtures / w["id"], w)
        for w in protocol["workloads"]
    }
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    user = pwd.getpwnam("scryntic")
    os.chown(args.evidence, user.pw_uid, user.pw_gid)
    os.umask(0o077)
    durable_json(
        args.evidence / "admission.json",
        {"identities": identities, "fixtures": fixtures},
    )
    randomizer = random.Random(protocol["order_seed"])
    records: list[dict[str, Any]] = []
    disabled: set[str] = set()
    for workload in protocol["workloads"]:
        for run in range(protocol["warmup_runs"] + protocol["measured_process_runs"]):
            candidates = list(protocol["candidates"])
            randomizer.shuffle(candidates)
            for candidate in candidates:
                sequence = len(records)
                if sequence >= protocol["max_attempts"]:
                    raise ValueError("PI05 campaign budget exhausted")
                intent = identities | {
                    "fixture_sha256": fixtures[workload["id"]],
                    "workload": workload["id"],
                    "rows": workload["rows"],
                    "gaps": workload["gaps"],
                    "candidate": candidate,
                    "run": run,
                    "warmup": run == 0,
                    "status": "started",
                }
                durable_json(args.evidence / f"intent-{sequence:03}.json", intent)
                durable_json(
                    args.evidence / "campaign.json",
                    {
                        "identities": identities,
                        "state": "incomplete",
                        "records": records,
                        "pending": intent,
                    },
                    replace=True,
                )
                if candidate in disabled:
                    result = {
                        "candidate": candidate,
                        "status": "unqualified",
                        "reason": "prior-candidate-failure-not-retried",
                    }
                else:
                    report = args.evidence / f"sample-{sequence:03}.json"
                    result = sample(
                        source,
                        args.fixtures / workload["id"],
                        report,
                        candidate,
                        sequence,
                    )
                    if result["status"] != "qualified":
                        disabled.add(candidate)
                if boot_file.read_text().strip() != boot_id:
                    result.update(status="unqualified", reason="boot-identity-changed")
                    disabled.update(protocol["candidates"])
                result = bind_result(intent, result)
                if result["status"] != "qualified":
                    disabled.add(candidate)
                records.append(result)
                durable_json(args.evidence / f"record-{sequence:03}.json", result)
                print(
                    json.dumps(
                        {
                            k: result[k]
                            for k in ("workload", "candidate", "run", "status")
                        }
                    ),
                    flush=True,
                )
    if source_identity(source) != identities[
        "source_tree_sha256"
    ] or runtime_identity() != {
        k: identities[k] for k in ("runtime_sha256", "python_sha256")
    }:
        raise ValueError("Executed source/runtime changed during campaign")
    final = {
        "identities": identities,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "state": "complete",
        "records": records,
        "decision": summarize(records, protocol),
    }
    durable_json(args.evidence / "campaign.json", final, replace=True)
    print(json.dumps(final["decision"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
