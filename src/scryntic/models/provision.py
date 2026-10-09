"""Operator-only provisioning. No inference or vendor package imports occur here."""

import argparse
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from scryntic.models.artifacts import snapshot_file
from scryntic.models.definitions import definition
from scryntic.models.inventory import RuntimeBundle, create_inventory
from scryntic.models.manifest import TIMESFM_ASSETS, ModelAssets
from scryntic.models.selection import supported_models


def import_artifacts(
    source: Path, destination: Path, assets: ModelAssets | None = None
) -> None:
    """Verify downloads against the owned manifest, into a new private directory."""
    assets = TIMESFM_ASSETS if assets is None else assets
    if not destination.is_absolute() or not source.is_absolute():
        raise ValueError("Provisioning paths must be absolute")
    destination.mkdir(mode=0o700)
    try:
        for name, checksum, size in assets.artifacts:
            snapshot_file(source / name, destination / name, checksum, size)
    except BaseException:
        for name, _, _ in assets.artifacts:
            (destination / name).unlink(missing_ok=True)
        destination.rmdir()
        raise


def write_inventory(
    runtime: Path, lock: Path, assets: ModelAssets | None = None
) -> str:
    """Explicit operator enrollment, never an automatic admission fallback."""
    raw = create_inventory(runtime, lock, assets)
    descriptor = os.open(
        runtime / "inventory.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="timesfm-2.5", choices=supported_models())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    artifacts = commands.add_parser("import-artifacts")
    artifacts.add_argument("--source", type=Path, required=True)
    artifacts.add_argument("--destination", type=Path, required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--runtime", type=Path, required=True)
    inventory.add_argument("--lock", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifacts", type=Path, required=True)
    verify.add_argument("--runtime", type=Path, required=True)
    verify.add_argument("--runtime-sha256", required=True)
    args = parser.parse_args()
    assets_spec = definition(args.model).assets
    result: dict[str, Any]
    if args.command == "list":
        result = {"supported_models": list(supported_models())}
    else:
        if assets_spec is None:
            raise ValueError("Fixture provider needs no external provisioning")
        if args.command == "import-artifacts":
            import_artifacts(args.source, args.destination, assets_spec)
            result = {
                "verified_artifacts": [name for name, _, _ in assets_spec.artifacts]
            }
        elif args.command == "inventory":
            result = {
                "runtime_sha256": write_inventory(args.runtime, args.lock, assets_spec)
            }
        else:
            bundle = RuntimeBundle.read(
                args.artifacts, args.runtime, args.runtime_sha256, assets_spec
            )
            result = {
                "runtime_sha256": bundle.inventory_sha256,
                "files": len(bundle.files),
            }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
