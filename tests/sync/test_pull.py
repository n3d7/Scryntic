import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.publication.coordinator import Published
from scryntic.publication.manifest import (
    ManifestStorage,
    parse_manifest,
    prepare_manifest,
)
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import PullError, PullLimits
from scryntic.sync.pull import pull
from tests.normalization.helpers import (
    DEFAULT_RECEIPT,
    fake_candle_payload,
    installation,
    raw_record,
)
from tests.publication.helpers import FixedNormalizationReader, configured_coordinator
from tests.sync.helpers import PathRemote, enrollment


def test_repeated_pulls_converge_and_discover_later_publications(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-a"),
        batch_records=1,
    )
    try:
        first = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            one = asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            assert one.anchor.sequence == 1
            assert one.complete
            reads = remote.object_reads
            repeated = asyncio.run(
                pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT)
            )
            assert repeated.imported == ()
            assert remote.object_reads == reads
            second = bundle.coordinator.publish_next()
            assert isinstance(second, Published)
            later = asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            assert later.anchor.sequence == 2
            assert catalog.count() == 2
    finally:
        bundle.close()


def test_plain_f16_acceptance_does_not_skip_transport_anchor(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path / "server")
    try:
        publication = bundle.coordinator.publish_next()
        assert isinstance(publication, Published)
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        data = ManifestStorage(bundle.root).read_exact(publication.manifest, 65536)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            # F16 alone validates data and grants no enrollment/anchor authority.
            incoming = tmp_path / "incoming"
            incoming.mkdir(mode=0o700)
            for item in parse_manifest(data, 65536).body.objects:
                source = (
                    remote.root
                    / "objects"
                    / "sha256"
                    / item.sha256[:2]
                    / (item.sha256 + ".parquet")
                )
                shutil.copyfile(source, incoming / item.sha256)
                (incoming / item.sha256).chmod(0o400)
            catalog.accept(data, incoming, DEFAULT_RECEIPT)
            catalog.enroll(endpoint)
            assert catalog.anchor(endpoint.name).sequence == 0
            result = asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            assert result.complete
            assert result.anchor.sequence == 1
            assert catalog.count() == 1
            assert remote.object_reads == 0
    finally:
        bundle.close()


def test_late_correction_and_bounded_batches_converge(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-a"),
        batch_records=1,
    )
    try:
        correction = raw_record(offset=5, payload=fake_candle_payload(close="101.00"))
        bundle.raw_reader.records = (bundle.records[0], correction)
        normalized = FixedNormalizationReader(bundle.raw_reader.records)
        bundle.normalization_reader.outcomes = normalized.outcomes
        bundle.normalization_reader.semantics = normalized.semantics
        first = bundle.coordinator.publish_next()
        second = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        assert isinstance(second, Published)
        docs = tuple(
            parse_manifest(
                ManifestStorage(bundle.root).read_exact(p.manifest, 65536), 65536
            )
            for p in (first, second)
        )
        original = json.loads(bundle.records[0].envelope.payload)
        corrected = json.loads(correction.envelope.payload)
        assert original["start_ns"] == corrected["start_ns"]
        assert original["close"] != corrected["close"]
        assert docs[0].body.objects != docs[1].body.objects
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            bounds = PullLimits(max_new_manifests=1)
            one = asyncio.run(
                pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT, bounds)
            )
            assert one.anchor.sequence == 1
            assert not one.complete
            two = asyncio.run(
                pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT, bounds)
            )
            assert two.anchor.sequence == 2
            assert two.complete
            assert catalog.inspect(second.manifest) == second.manifest
    finally:
        bundle.close()


@pytest.mark.parametrize("attack", ["missing", "corrupt"])
def test_object_failure_never_advances_catalog(tmp_path: Path, attack: str) -> None:
    bundle = configured_coordinator(tmp_path / "server")
    try:
        published = bundle.coordinator.publish_next()
        assert isinstance(published, Published)
        data = ManifestStorage(bundle.root).read_exact(published.manifest, 65536)
        item = parse_manifest(data, 65536).body.objects[0]
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        path = (
            remote.root
            / "objects"
            / "sha256"
            / item.sha256[:2]
            / (item.sha256 + ".parquet")
        )
        if attack == "missing":
            path.unlink()
        else:
            path.chmod(0o600)
            path.write_bytes(b"x" * item.encoded_bytes)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            prepared_operation = pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT)
            with pytest.raises(PullError):
                asyncio.run(prepared_operation)
            assert catalog.count() == 0
            assert catalog.anchor(endpoint.name).sequence == 0
    finally:
        bundle.close()


def test_interrupted_object_resumes_without_advancing_anchor(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path / "server")
    try:
        assert isinstance(bundle.coordinator.publish_next(), Published)
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        remote.interrupt_after = 2
        limits = PullLimits(chunk_bytes=1024)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            prepared_operation = pull(
                catalog, endpoint.name, remote, DEFAULT_RECEIPT, limits
            )
            with pytest.raises(PullError):
                asyncio.run(prepared_operation)
            assert catalog.anchor(endpoint.name).sequence == 0
            assert catalog.count() == 0
            remote.interrupt_after = None
            remote.reads.clear()
            result = asyncio.run(
                pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT, limits)
            )
            assert result.anchor.sequence == 1
            object_requests = [
                (p, o) for p, o, _ in remote.reads if p.startswith("objects/")
            ]
            assert object_requests[0][1] == 1024
    finally:
        bundle.close()


@pytest.mark.parametrize(
    "attack", ["truncate", "rewrite", "predecessor", "gap", "epoch"]
)
def test_history_incidents_preserve_accepted_anchor(
    tmp_path: Path, attack: str
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-a"),
        batch_records=1,
    )
    try:
        first = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        endpoint = enrollment()
        storage = ManifestStorage(bundle.root)
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            anchor = catalog.anchor(endpoint.name)
            if attack == "truncate":
                storage.manifest_path(first.manifest).unlink()
            elif attack == "rewrite":
                path = storage.manifest_path(first.manifest)
                data = path.read_bytes()
                path.chmod(0o600)
                path.write_bytes(data.replace(b'"collector-a"', b'"collector-b"'))
            else:
                second = bundle.coordinator.publish_next()
                assert isinstance(second, Published)
                if attack == "gap":
                    storage.manifest_path(first.manifest).unlink()
                else:
                    data = storage.read_exact(second.manifest, 65536)
                    body = parse_manifest(data, 65536).body
                    body = (
                        replace(body, previous_manifest_hash="a" * 64)
                        if attack == "predecessor"
                        else replace(
                            body,
                            epoch="epoch-other",
                            first_ingestion=replace(
                                body.first_ingestion, epoch="epoch-other"
                            ),
                            last_ingestion=replace(
                                body.last_ingestion, epoch="epoch-other"
                            ),
                            checkpoint_after=replace(
                                body.checkpoint_after, epoch="epoch-other"
                            ),
                        )
                    )
                    changed = prepare_manifest(body)
                    reference = parse_manifest(changed, 65536).ref
                    if attack == "predecessor":
                        storage.manifest_path(second.manifest).unlink()
                    storage.install_exact(changed, reference)
            prepared_operation = pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT)
            with pytest.raises(PullError):
                asyncio.run(prepared_operation)
            assert catalog.anchor(endpoint.name) == anchor
            assert catalog.count() == 1
    finally:
        bundle.close()


def test_unenrolled_catalog_cannot_pull(tmp_path: Path) -> None:
    remote = PathRemote(tmp_path, enrollment())
    with PullCatalog(installation(tmp_path / "client")) as catalog:
        prepared_operation = pull(catalog, "server-a", remote, DEFAULT_RECEIPT)
        with pytest.raises(PullError):
            asyncio.run(prepared_operation)


def test_stale_hint_never_hides_new_chain_tail(tmp_path: Path) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-a"),
        batch_records=1,
    )
    try:
        first = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        assert isinstance(bundle.coordinator.publish_next(), Published)
        endpoint = enrollment()
        root = bundle.root.state_dir / "archive"
        (root / "head.json").write_bytes(
            canonical_json_bytes(
                {
                    "producer": first.manifest.producer,
                    "epoch": first.manifest.epoch,
                    "sequence": first.manifest.sequence,
                    "manifest_hash": first.manifest.manifest_hash,
                }
            )
        )
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            result = asyncio.run(
                pull(
                    catalog, endpoint.name, PathRemote(root, endpoint), DEFAULT_RECEIPT
                )
            )
            assert result.anchor.sequence == 2
            assert result.freshness == "degraded"
            assert result.complete
    finally:
        bundle.close()


def test_checkpoint_bootstrap_and_trust_restore_rehydrate_only_available_coverage(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-a"),
        batch_records=1,
    )
    try:
        first = bundle.coordinator.publish_next()
        assert isinstance(first, Published)
        storage = ManifestStorage(bundle.root)
        checkpoint = storage.read_exact(first.manifest, 65536)
        document = parse_manifest(checkpoint, 65536)
        assert isinstance(bundle.coordinator.publish_next(), Published)
        for descriptor in document.body.objects:
            (
                bundle.root.state_dir
                / "archive/objects/sha256"
                / descriptor.sha256[:2]
                / (descriptor.sha256 + ".parquet")
            ).unlink()
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint, checkpoint)
            result = asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            assert result.anchor.sequence == 2
            assert catalog.count() == 1
            backup = catalog.backup()
        with PullCatalog(installation(tmp_path / "restored")) as restored:
            restored.restore(backup)
            assert restored.count() == 0
            anchor = restored.anchor(endpoint.name)
            result = asyncio.run(pull(restored, endpoint.name, remote, DEFAULT_RECEIPT))
            assert result.anchor == anchor
            assert restored.count() == 1
            assert result.complete
    finally:
        bundle.close()


def test_epoch_reconciliation_requires_operator_and_preserves_old_anchor(
    tmp_path: Path,
) -> None:
    bundle = configured_coordinator(
        tmp_path / "server",
        offsets=(2, 5),
        epochs=("epoch-a", "epoch-b"),
        batch_records=1,
    )
    try:
        assert isinstance(bundle.coordinator.publish_next(), Published)
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            old = catalog.anchor(endpoint.name)
            assert isinstance(bundle.coordinator.publish_next(), Published)
            prepared_operation = pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT)
            with pytest.raises(PullError):
                asyncio.run(prepared_operation)
            assert catalog.anchor(endpoint.name) == old
            catalog.authorize_epoch(endpoint.name, "epoch-b")
            remote.enrollment = catalog.enrollment(endpoint.name)
            result = asyncio.run(pull(catalog, endpoint.name, remote, DEFAULT_RECEIPT))
            assert result.anchor.epoch == "epoch-b"
            assert result.anchor.sequence == 1
            assert catalog.anchor(endpoint.name, "epoch-a") == old
    finally:
        bundle.close()


def test_wire_budget_cannot_skip_an_object(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path / "server")
    try:
        assert isinstance(bundle.coordinator.publish_next(), Published)
        endpoint = enrollment()
        remote = PathRemote(bundle.root.state_dir / "archive", endpoint)
        with PullCatalog(installation(tmp_path / "client")) as catalog:
            catalog.enroll(endpoint)
            prepared_operation = pull(
                catalog,
                endpoint.name,
                remote,
                DEFAULT_RECEIPT,
                PullLimits(max_transfer_bytes=1),
            )
            with pytest.raises(PullError):
                asyncio.run(prepared_operation)
            assert catalog.count() == 0
            assert catalog.anchor(endpoint.name).sequence == 0
    finally:
        bundle.close()


def test_real_sftp_pull_validates_in_restricted_worker(tmp_path: Path) -> None:
    from scryntic.sync.sftp import SFTPRemote
    from tests.sync.test_sftp import endpoint as served_endpoint

    bundle = configured_coordinator(tmp_path / "server")
    try:
        assert isinstance(bundle.coordinator.publish_next(), Published)
        network = tmp_path / "network"
        network.mkdir(mode=0o700)

        async def scenario() -> None:
            async with served_endpoint(network) as (adapter, auth, root, _):
                shutil.copytree(
                    bundle.root.state_dir / "archive", root, dirs_exist_ok=True
                )
                value = replace(
                    adapter.enrollment, producer="collector-a", epoch="epoch-a"
                )
                with PullCatalog(installation(tmp_path / "client")) as catalog:
                    catalog.enroll(value)
                    async with SFTPRemote(
                        value, adapter._installation, adapter._configuration
                    ) as remote:
                        result = await pull(
                            catalog, value.name, remote, DEFAULT_RECEIPT
                        )
                        assert result.complete
                        assert result.anchor.sequence == 1
                        assert catalog.count() == 1
                    assert auth == ["reader"]
                    assert catalog.inspect(result.imported[0]) == result.imported[0]

        asyncio.run(scenario())
    finally:
        bundle.close()
