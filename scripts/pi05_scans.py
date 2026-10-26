"""Prepare synthetic inputs or execute one closed PI05 qualification job."""

import argparse
import json
import os
from pathlib import Path

from scryntic.analytics.benchmark import run_fixture
from scryntic.analytics.launcher import CANDIDATES
from scryntic.archive.canonical import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--rows", type=int, choices=(512, 4096), default=512)
    parser.add_argument("--gaps", action="store_true")
    parser.add_argument("--candidate", choices=CANDIDATES, default="pyarrow-scan")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.operation == "prepare":
        from scryntic.analytics.fixtures import prepare_fixture

        result = prepare_fixture(args.path, count=args.rows, gaps=args.gaps)
        print(json.dumps({k: v for k, v in result.items() if k != "expected_rows"}))
        return
    try:
        result = run_fixture(args.path, args.candidate)
    except Exception:
        # Worker/native diagnostics and fixture values are never in reports.
        result = {
            "schema": "scryntic.pi05.scan-result.v1",
            "candidate": args.candidate,
            "status": "unqualified",
            "reason": "candidate-or-control-failed",
        }
    data = canonical_json_bytes(result)
    if args.report is not None:
        with args.report.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    print(data.decode())
    if result["status"] != "qualified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
