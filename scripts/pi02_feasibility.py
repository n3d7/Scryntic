"""Run the registered PI02 gate on already retained local archives.

This script does not download data, fit models, or open the final period.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.research.feasibility import run_registered_feasibility
from scryntic.research.ledger import ResearchLedger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uid = os.geteuid()
    state_dir = args.state_dir.absolute()
    installation = Installation(
        config_dir=state_dir,
        state_dir=state_dir,
        runtime_dir=state_dir,
        credential_dir=state_dir,
        owner_uid=uid,
        config_uid=uid,
        service_mode=False,
    )
    if not ResearchLedger.active(installation):
        parser.error("An active PI01 research ledger is required")
    with ResearchLedger(installation) as ledger:
        report = run_registered_feasibility(
            args.policy,
            args.spec,
            args.archive_root,
            ledger,
        )
    payload = canonical_json_bytes(report) + b"\n"
    fd = os.open(
        args.output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
