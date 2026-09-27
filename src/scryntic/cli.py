"""Small local F09 demonstration CLI over the composition service."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from scryntic.composition import run_fake_slice
from scryntic.configuration.paths import Installation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the trusted F09 source-to-model slice"
    )
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--lock-file", type=Path, default=Path("uv.lock"))
    parser.add_argument("--code-revision", default="f09-fake-v1")
    parser.add_argument(
        "--provider", choices=("persistence", "trend"), default="persistence"
    )
    parser.add_argument("--horizon", type=int, default=2)
    args = parser.parse_args(argv)
    result = run_fake_slice(
        Installation.workstation(home=args.home, environment={}),
        epoch=args.epoch,
        code_revision=args.code_revision,
        dependency_lock_sha256=sha256(args.lock_file.read_bytes()).hexdigest(),
        provider_name=args.provider,
        horizon=args.horizon,
    )
    print(
        json.dumps(
            {
                "dataset_manifest_sha256": result.dataset.manifest_sha256,
                "dataset_rows": result.dataset.row_count,
                "forecast_artifact_sha256": result.forecast.sha256,
                "provider_id": result.forecast.provider_id,
                "model_revision": result.forecast.model_revision,
            },
            sort_keys=True,
        )
    )
    return 0
