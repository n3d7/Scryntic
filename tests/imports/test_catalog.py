"""Actual restricted decoding and all-or-nothing local registration."""

import shutil
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.domain.identity import SchemaRef, Version
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError, ImportLimits, parse_request
from scryntic.publication.coordinator import Published
from scryntic.publication.manifest import ManifestStorage, prepare_manifest
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.publication.helpers import configured_coordinator


def published_inputs(tmp_path: Path) -> tuple[bytes, Path]:
    bundle = configured_coordinator(
        tmp_path / "collector", offsets=(2, 5), epochs=("epoch-a", "epoch-a")
    )
    try:
        result = bundle.coordinator.publish_next()
        assert isinstance(result, Published)
        data = ManifestStorage(bundle.root).read_exact(result.manifest, 65536)
        document = parse_request(data, ImportLimits())
        incoming = tmp_path / "incoming"
        incoming.mkdir(mode=0o700)
        for descriptor in document.body.objects:
            source = (
                bundle.root.state_dir
                / "archive/objects/sha256"
                / descriptor.sha256[:2]
                / f"{descriptor.sha256}.parquet"
            )
            shutil.copyfile(source, incoming / descriptor.sha256)
            (incoming / descriptor.sha256).chmod(0o600)
        return data, incoming
    finally:
        bundle.close()


def test_accept_reopen_and_reinspect_always_decode(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        reference = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 1
        assert catalog.accept(data, incoming, DEFAULT_RECEIPT) == reference
        assert catalog.count() == 1
        assert catalog.inspect(reference) == reference
    with ImportCatalog(root) as catalog:
        assert catalog.count() == 1
        assert catalog.inspect(reference) == reference
        # A previously accepted catalog row never bypasses later decoding.
        descriptor = parse_request(data, ImportLimits()).body.objects[0]
        stored = root.state_dir / "imports/objects" / descriptor.sha256
        stored.chmod(0o600)
        stored.write_bytes(b"corrupted after acceptance")
        stored.chmod(0o400)
        with pytest.raises(ImportError):
            catalog.inspect(reference)
        assert catalog.count() == 1


def test_corruption_cannot_register_or_publish(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    descriptor = parse_request(data, ImportLimits()).body.objects[0]
    (incoming / descriptor.sha256).write_bytes(b"corrupt")
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        with pytest.raises(ImportError):
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0
        assert list((root.state_dir / "imports/objects").iterdir()) == []


def test_one_catalog_owner(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with ImportCatalog(root):
        with pytest.raises(ImportError):
            ImportCatalog(root)


def test_unsafe_catalog_file_fails_closed(tmp_path: Path) -> None:
    root = installation(tmp_path)
    target = root.state_dir / "imports"
    target.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"unchanged")
    (target / "catalog.sqlite3").symlink_to(outside)
    with pytest.raises(ImportError):
        ImportCatalog(root)
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "attack",
    [
        "duplicate-key",
        "schema",
        "codec",
        "traversal",
        "noncanonical",
        "decoded-limit",
        "record-limit",
        "encoded-limit",
        "native-corruption",
    ],
)
def test_hostile_import_never_advances_catalog(tmp_path: Path, attack: str) -> None:
    data, incoming = published_inputs(tmp_path)
    document = parse_request(data, ImportLimits())
    raw, normalized = document.body.objects
    limits = ImportLimits()
    if attack == "duplicate-key":
        data = data.replace(b'{"body":', b'{"body":{},"body":', 1)
    elif attack == "noncanonical":
        data += b"\n"
    elif attack == "traversal":
        data = data.replace(raw.sha256.encode(), b"../outside")
    elif attack == "schema":
        data = prepare_manifest(
            replace(
                document.body,
                objects=(
                    replace(raw, format=SchemaRef(raw.format.name, Version(2, 0))),
                    normalized,
                ),
            )
        )
    elif attack == "codec":
        data = prepare_manifest(
            replace(
                document.body,
                objects=(replace(raw, codec="framed-zlib-v1"), normalized),
            )
        )
    elif attack == "decoded-limit":
        limits = replace(limits, max_decoded_bytes=1)
    elif attack == "record-limit":
        limits = replace(limits, max_records=1)
    elif attack == "encoded-limit":
        limits = replace(limits, max_encoded_bytes=1)
    else:
        # Rehash/redeclare damaged bytes: digest conformance cannot make a
        # malformed native format safe or bypass the decoder boundary.
        damaged = (incoming / raw.sha256).read_bytes()[:-8] + b"BADFOOT!"
        new_hash = sha256(damaged).hexdigest()
        (incoming / new_hash).write_bytes(damaged)
        (incoming / new_hash).chmod(0o600)
        data = prepare_manifest(
            replace(document.body, objects=(replace(raw, sha256=new_hash), normalized))
        )
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root, limits) as catalog:
        with pytest.raises(ImportError):
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0
        assert list((root.state_dir / "imports/objects").iterdir()) == []


@pytest.mark.parametrize("failure", ["unavailable", "timeout", "malformed", "flood"])
def test_worker_failure_cannot_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from scryntic.imports.launcher import LinuxDecoder

    data, incoming = published_inputs(tmp_path)
    if failure in ("unavailable", "timeout"):

        def broken_command(
            self: LinuxDecoder, inputs: tuple[int, ...], *, probe: bool
        ) -> list[str]:
            import sys

            return (
                ["/unavailable/bwrap"]
                if failure == "unavailable"
                else [sys.executable, "-I", "-c", "import time; time.sleep(10)"]
            )

        monkeypatch.setattr(LinuxDecoder, "_command", broken_command)
    else:

        def bad_result(self: LinuxDecoder, inputs: tuple[int, int, int]) -> bytes:
            return b'{"protocol":true}' if failure == "malformed" else b"x" * 65537

        monkeypatch.setattr(LinuxDecoder, "decode", bad_result)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root, ImportLimits(wall_seconds=1)) as catalog:
        with pytest.raises(ImportError):
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0
        assert list((root.state_dir / "imports/objects").iterdir()) == []


def test_database_failure_retains_orphans_without_acceptance(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    budget = sum(
        item.encoded_bytes for item in parse_request(data, ImportLimits()).body.objects
    )
    limits = ImportLimits(max_storage_bytes=budget)
    with ImportCatalog(root, limits) as catalog:

        def reject_insert(
            operation: int,
            first: str | None,
            second: str | None,
            database: str | None,
            trigger: str | None,
        ) -> int:
            return (
                sqlite3.SQLITE_DENY
                if operation == sqlite3.SQLITE_INSERT and first == "imports"
                else sqlite3.SQLITE_OK
            )

        catalog._db.set_authorizer(reject_insert)
        with pytest.raises(ImportError):
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
        catalog._db.set_authorizer(None)
        assert catalog.count() == 0
    with ImportCatalog(root, limits) as catalog:
        catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 1


def test_crash_after_object_link_recovers_pending_hardlink(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(data)
    code = """
import os,sys
from pathlib import Path
from scryntic.imports.catalog import ImportCatalog
from scryntic.configuration.paths import Installation
from scryntic.domain.time import ClockSample,TimeQuality
root=Path(sys.argv[1]); uid=os.geteuid()
installation=Installation(root/'config',root/'state',root/'runtime',root/'config/credentials',uid,uid,False)
catalog=ImportCatalog(installation)
original=os.fsync
def stop_after_link(fd):
    original(fd)
    names=os.listdir(catalog._objects)
    if any(n.startswith('pending-') for n in names) and any(len(n)==64 for n in names):
        os._exit(77)
os.fsync=stop_after_link
catalog.accept(Path(sys.argv[3]).read_bytes(),Path(sys.argv[2]),ClockSample(1,0,'session',TimeQuality('clock')))
os._exit(1)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(root.state_dir.parent),
            str(incoming),
            str(manifest),
        ]
    )
    assert result.returncode == 77
    with ImportCatalog(root) as catalog:
        assert catalog.count() == 0
        catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 1
        assert all(len(name) == 64 for name in os.listdir(catalog._objects))


def test_storage_quota_never_advances_catalog(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root, ImportLimits(max_storage_bytes=1)) as catalog:
        with pytest.raises(ImportError):
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0


def test_local_clock_evidence_is_retained_without_upgrading_remote_claims(
    tmp_path: Path,
) -> None:
    import json

    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        catalog.accept(data, incoming, DEFAULT_RECEIPT)
        manifest, receipt = catalog._db.execute(
            "SELECT manifest,receipt FROM imports"
        ).fetchone()
        assert manifest == data
        claim = json.loads(receipt)
        assert claim["quality"]["status"] == "unknown"
        assert claim["wall_time_ns"] == DEFAULT_RECEIPT.wall_time_ns
