"""Offline selected-provider comparison against the same F19 replay cases."""

import argparse
import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path

from scryntic.configuration.paths import Installation
from scryntic.dataset.service import DatasetService
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import SchemaRef, Version
from scryntic.imports.catalog import ImportCatalog
from scryntic.models.evaluation import evaluate_selected
from scryntic.models.selection import ModelSelection
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import ReplayService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--per-partition", type=int, default=2)
    args = parser.parse_args()
    capture = json.loads((args.dataset_root / "capture.json").read_bytes())
    row = capture["dataset"]
    reference = DatasetRef(
        row["manifest_sha256"],
        SchemaRef(row["schema"]["name"], Version(**row["schema"]["version"])),
        row["row_count"],
    )
    installation = Installation.workstation(
        home=args.dataset_root / "home", environment={}
    )
    lock = sha256(Path("uv.lock").read_bytes()).hexdigest()
    with ImportCatalog(installation) as catalog:
        datasets = DatasetService(
            catalog,
            installation,
            code_revision=args.code_revision,
            dependency_lock_sha256=lock,
        )
        replay = ReplayService(
            datasets, code_revision=args.code_revision, dependency_lock_sha256=lock
        )
        prepared = replay.prepare(reference, ReplayConfig(**capture["replay"]))
        report = asyncio.run(
            evaluate_selected(
                prepared,
                reference,
                ModelSelection.read(args.configuration),
                installation,
                per_partition=args.per_partition,
            )
        )
    descriptor = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(report.canonical_bytes)
    print(
        json.dumps(
            {"evaluation_sha256": report.sha256, "output": str(args.output)},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
