"""One registered administrative recovery; original measured source stays frozen."""

import argparse
import hashlib
import json
import os
import platform
import random
import sys
from pathlib import Path
from typing import Any

from scryntic.analytics.decision import summarize
from scryntic.analytics.provenance import (
    file_hash,
    fixture_identity,
    runtime_identity,
    source_identity,
)


def claim_once(path: Path, value: dict[str, Any]) -> None:
    """An interrupted claim remains consumed; a new evidence directory cannot retry."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def registered_schedule(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    randomizer = random.Random(protocol["order_seed"])
    schedule = []
    for workload in protocol["workloads"]:
        for run in range(protocol["warmup_runs"] + protocol["measured_process_runs"]):
            candidates = list(protocol["candidates"])
            randomizer.shuffle(candidates)
            for candidate in candidates:
                schedule.append(
                    {
                        "workload": workload["id"],
                        "rows": workload["rows"],
                        "gaps": workload["gaps"],
                        "candidate": candidate,
                        "run": run,
                        "warmup": run == 0,
                    }
                )
    return schedule


def recovery_plan(
    previous: dict[str, Any], protocol: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    schedule = registered_schedule(protocol)
    records = previous["records"]
    pending = previous.get("pending", {})
    if (
        previous["state"] != "incomplete"
        or len(schedule) != 72
        or protocol["max_attempts"] != 72
        or len(records) != 59
        or pending.get("status") != "started"
        or any(pending.get(k) != v for k, v in schedule[59].items())
        or any(
            row.get("status") != "qualified"
            or any(row.get(k) != v for k, v in expected.items())
            for row, expected in zip(records, schedule, strict=False)
        )
    ):
        raise ValueError("Recovery requires the registered operator-stop checkpoint")
    return (
        records[:48],
        records[48:] + [pending | {"status": "operator-cancelled"}],
        schedule[48:],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "fixtures", "previous", "evidence", "amendment"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or "ID=debian" not in Path("/etc/os-release").read_text():
        parser.error("Explicit Debian operator execution required")
    source = args.source.resolve()
    # Import the verified original controller, not a modified measurement helper.
    sys.path.insert(0, str(source))
    from scripts import pi05_campaign as campaign

    amendment = json.loads(args.amendment.read_bytes())
    protocol_data = (source / "docs/validation/pi05/protocol-v1.json").read_bytes()
    protocol = json.loads(protocol_data)
    previous = json.loads(args.previous.read_bytes())
    retained, excluded, schedule = recovery_plan(previous, protocol)
    old = previous["identities"]
    if (
        amendment["previous_campaign_sha256"] != file_hash(args.previous)
        or amendment["protocol_sha256"] != hashlib.sha256(protocol_data).hexdigest()
        or amendment["original_consumed_attempts"] != 60
        or amendment["recovery_attempts"] != len(schedule)
        or amendment["maximum_total_attempts"] != 84
        or amendment["controller_sha256"] != file_hash(Path(__file__).resolve())
        or source_identity(source) != old["source_tree_sha256"]
        or file_hash(source / "uv.lock") != old["dependency_lock_sha256"]
        or runtime_identity()
        != {k: old[k] for k in ("runtime_sha256", "python_sha256")}
    ):
        raise ValueError("Recovery admission identity mismatch")
    fixtures = {
        w["id"]: fixture_identity(args.fixtures / w["id"], w)
        for w in protocol["workloads"]
    }
    if any(r["fixture_sha256"] != fixtures[r["workload"]] for r in excluded + retained):
        raise ValueError("Recovery fixture identity mismatch")
    boot_file = Path("/proc/sys/kernel/random/boot_id")
    identities = old | {"boot_id": boot_file.read_text().strip()}
    disposition = {
        "schema": "scryntic.pi05.recovery.v1",
        "amendment_sha256": file_hash(args.amendment),
        "controller_sha256": amendment["controller_sha256"],
        "original_attempts": 60,
        "excluded_original_partial_workload": excluded,
        "maximum_total_attempts": 84,
        "reason": "operator stop followed by host reboot; complete within-workload boot epochs only",
    }
    os.umask(0o077)
    claim_once(
        Path("/var/lib/scryntic-pi05-recovery")
        / (amendment["previous_campaign_sha256"] + ".json"),
        disposition | {"evidence": str(args.evidence.resolve())},
    )
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    import pwd

    user = pwd.getpwnam("scryntic")
    os.chown(args.evidence, user.pw_uid, user.pw_gid)
    campaign.durable_json(args.evidence / "admission.json", disposition)
    records = list(retained)
    for index, scheduled in enumerate(schedule):
        if boot_file.read_text().strip() != identities["boot_id"]:
            raise ValueError("Recovery host rebooted; no automatic retries")
        intent = (
            identities
            | scheduled
            | {
                "fixture_sha256": fixtures[scheduled["workload"]],
                "status": "started",
            }
        )
        sequence = 60 + index
        campaign.durable_json(args.evidence / f"intent-{sequence:03}.json", intent)
        campaign.durable_json(
            args.evidence / "campaign.json",
            {
                "identities": identities,
                "state": "incomplete",
                "records": records,
                "pending": intent,
                "recovery": disposition | {"total_attempts_started": sequence + 1},
            },
            replace=True,
        )
        result = campaign.bind_result(
            intent,
            campaign.sample(
                source,
                args.fixtures / scheduled["workload"],
                args.evidence / f"sample-{sequence:03}.json",
                scheduled["candidate"],
                sequence,
            ),
        )
        if boot_file.read_text().strip() != identities["boot_id"]:
            result.update(status="unqualified", reason="boot-identity-changed")
        records.append(result)
        campaign.durable_json(args.evidence / f"record-{sequence:03}.json", result)
        campaign.durable_json(
            args.evidence / "campaign.json",
            {
                "identities": identities,
                "state": "incomplete",
                "records": records,
                "pending": None,
                "recovery": disposition | {"total_attempts_started": sequence + 1},
            },
            replace=True,
        )
        print(
            json.dumps({k: result[k] for k in ("candidate", "run", "status")}),
            flush=True,
        )
        if result["status"] != "qualified":
            raise ValueError("Recovery candidate failure retained; no retries")
    if source_identity(source) != old["source_tree_sha256"] or runtime_identity() != {
        k: old[k] for k in ("runtime_sha256", "python_sha256")
    }:
        raise ValueError("Recovery source/runtime changed")
    final = {
        "identities": identities,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "state": "complete",
        "records": records,
        "recovery": disposition | {"total_attempts_started": 84},
        "decision": summarize(records, protocol),
    }
    campaign.durable_json(args.evidence / "campaign.json", final, replace=True)
    print(json.dumps(final["decision"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
