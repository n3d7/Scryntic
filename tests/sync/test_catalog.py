"""Durable explicit trust and transactional F16 transport progress."""

import base64
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep

import pytest

from scryntic.imports.launcher import LinuxDecoder
from scryntic.imports.protocol import ImportError, ImportLimits, parse_request
from scryntic.publication.coordinator import Published
from scryntic.publication.manifest import (
    GENESIS_MANIFEST_HASH,
    ManifestStorage,
    prepare_manifest,
)
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import Enrollment, PullError, PullLimits
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import DEFAULT_RECEIPT, installation
from tests.publication.helpers import configured_coordinator


def endpoint(data: bytes) -> Enrollment:
    body = parse_request(data, ImportLimits()).body
    key = base64.b64encode(
        b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"k" * 32
    ).decode()
    return Enrollment(
        "server",
        "127.0.0.1",
        22,
        "reader",
        "/artifacts",
        f"ssh-ed25519 {key}",
        body.producer,
        body.epoch,
    )


def test_enrollment_reopen_and_real_atomic_acceptance(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    value = endpoint(data)
    with PullCatalog(root) as catalog:
        catalog.enroll(value)
        assert catalog.anchor(value.name).sequence == 0
        reference = catalog.accept_next(value.name, data, incoming, DEFAULT_RECEIPT)
        assert catalog.anchor(value.name).reference == reference
        assert catalog.history(value.name) == (data,)
        assert (
            catalog.accept_next(value.name, data, incoming, DEFAULT_RECEIPT)
            == reference
        )
    with PullCatalog(root) as catalog:
        assert catalog.enrollment(value.name) == value
        assert catalog.anchor(value.name).reference == reference
        assert catalog.inspect(reference) == reference


def test_plain_acceptance_grants_no_transport_trust(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.accept(data, incoming, DEFAULT_RECEIPT)
        with pytest.raises(PullError):
            catalog.anchor("server")
        catalog.enroll(endpoint(data))
        assert catalog.anchor("server").manifest_hash == GENESIS_MANIFEST_HASH
        catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert catalog.anchor("server").sequence == 1
        assert catalog.count() == 1


def test_checkpoint_backup_restore_is_trust_only(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "first")) as first:
        first.enroll(endpoint(data), checkpoint=data)
        assert first.count() == 0
        backup = first.backup()
    with PullCatalog(installation(tmp_path / "second")) as second:
        second.restore(backup)
        second.restore(backup)
        assert second.backup() == backup
        assert second.count() == 0
        assert second.history("server") == (data,)
        reference = second.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert second.is_imported(reference)


def test_registration_sql_failure_preserves_import_and_anchor(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))

        def reject_update(action: int, first: str | None, *_: object) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and first == "pull_epochs"
                else sqlite3.SQLITE_OK
            )

        catalog._db.set_authorizer(reject_update)
        with pytest.raises(ImportError):
            catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        catalog._db.set_authorizer(None)
        assert catalog.count() == 0
        assert catalog.anchor("server").sequence == 0
        assert catalog.history("server") == ()
        catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 1
        assert catalog.anchor("server").sequence == 1


def test_chain_and_explicit_epoch_boundaries(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    body = parse_request(data, ImportLimits()).body
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))
        gap = prepare_manifest(
            replace(body, sequence=3, previous_manifest_hash="a" * 64)
        )
        with pytest.raises(PullError):
            catalog.accept_next("server", gap, incoming, DEFAULT_RECEIPT)
        catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        catalog.authorize_epoch("server", "new-epoch")
        assert catalog.epochs("server") == (body.epoch, "new-epoch")
        assert catalog.anchor("server", body.epoch).sequence == 1
        assert catalog.anchor("server").sequence == 0
        with pytest.raises(PullError):
            catalog.authorize_epoch("server", body.epoch)


@pytest.mark.parametrize(
    "attack",
    ["duplicate", "noncanonical", "version", "pin", "rollback", "hash", "host-type"],
)
def test_restore_rejects_conflicts_atomically(tmp_path: Path, attack: str) -> None:
    data, incoming = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))
        old = catalog.backup()
        catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        before = catalog.backup()
        if attack == "rollback":
            changed = old
        elif attack == "duplicate":
            changed = before.replace(b"{", b'{"version":1,', 1)
        elif attack == "noncanonical":
            changed = before + b"\n"
        elif attack == "hash":
            changed = before.replace(
                parse_request(data, ImportLimits()).manifest_hash.encode(), b"a" * 64
            )
        else:
            content = json.loads(before)
            if attack == "version":
                content["version"] = True
            elif attack == "host-type":
                content["enrollments"][0]["endpoint"]["host"] = 2130706433
            else:
                content["enrollments"][0]["endpoint"]["host"] = "127.0.0.2"
            changed = json.dumps(
                content, sort_keys=True, separators=(",", ":")
            ).encode()
        with pytest.raises(PullError):
            catalog.restore(changed)
        assert catalog.backup() == before


def test_bounded_backup_and_enrollment(tmp_path: Path) -> None:
    data, _ = published_inputs(tmp_path)
    with PullCatalog(
        installation(tmp_path / "workstation"),
        pull_limits=PullLimits(max_entries=1, max_backup_bytes=1),
    ) as catalog:
        catalog.enroll(endpoint(data))
        second = replace(endpoint(data), name="second")
        with pytest.raises(PullError):
            catalog.enroll(second)
        with pytest.raises(PullError):
            catalog.backup()


def published_chain(
    tmp_path: Path, *, cross_epoch: bool = False
) -> tuple[tuple[bytes, ...], Path]:
    epochs = ("epoch-a", "epoch-b") if cross_epoch else ("epoch-a", "epoch-a")
    bundle = configured_coordinator(
        tmp_path / "collector", offsets=(2, 5), epochs=epochs, batch_records=1
    )
    incoming = tmp_path / "incoming"
    incoming.mkdir(mode=0o700)
    result: list[bytes] = []
    try:
        for _ in range(2):
            published = bundle.coordinator.publish_next()
            assert isinstance(published, Published)
            data = ManifestStorage(bundle.root).read_exact(published.manifest, 65536)
            result.append(data)
            for descriptor in parse_request(data, ImportLimits()).body.objects:
                source = (
                    bundle.root.state_dir
                    / "archive/objects/sha256"
                    / descriptor.sha256[:2]
                    / f"{descriptor.sha256}.parquet"
                )
                shutil.copyfile(source, incoming / descriptor.sha256)
                (incoming / descriptor.sha256).chmod(0o600)
        return tuple(result), incoming
    finally:
        bundle.close()


def test_ordered_chain_restore_and_exact_rehydration(tmp_path: Path) -> None:
    (first, second), incoming = published_chain(tmp_path)
    root = installation(tmp_path / "workstation")
    with PullCatalog(root) as catalog:
        catalog.enroll(endpoint(first))
        reference = catalog.accept_next("server", first, incoming, DEFAULT_RECEIPT)
        original = catalog.backup()
        second_ref = catalog.accept_next("server", second, incoming, DEFAULT_RECEIPT)
        advanced = catalog.backup()
        assert catalog.history("server") == (first, second)
    with PullCatalog(installation(tmp_path / "restored")) as restored:
        restored.restore(original)
        restored.restore(advanced)
        assert restored.count() == 0
        assert restored.anchor("server").reference == second_ref
        restored.accept_next("server", first, incoming, DEFAULT_RECEIPT)
        assert restored.anchor("server").reference == second_ref
        restored.accept_next("server", second, incoming, DEFAULT_RECEIPT)
        assert restored.count() == 2
        assert restored.inspect(reference) == reference
        assert restored.backup() == advanced
        with pytest.raises(PullError):
            restored.restore(original)


def test_cross_epoch_checkpoint_continuity_and_backup(tmp_path: Path) -> None:
    (first, second), incoming = published_chain(tmp_path, cross_epoch=True)
    body = parse_request(second, ImportLimits()).body
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(first))
        first_ref = catalog.accept_next("server", first, incoming, DEFAULT_RECEIPT)
        catalog.authorize_epoch("server", body.epoch)
        invalid = prepare_manifest(replace(body, checkpoint_before=None))
        with pytest.raises(PullError):
            catalog.accept_next("server", invalid, incoming, DEFAULT_RECEIPT)
        second_ref = catalog.accept_next("server", second, incoming, DEFAULT_RECEIPT)
        backup = catalog.backup()
        assert catalog.anchor("server", first_ref.epoch).reference == first_ref
    with PullCatalog(installation(tmp_path / "restored")) as restored:
        restored.restore(backup)
        assert restored.anchor("server").reference == second_ref
        assert restored.history("server") == (first, second)


def test_control_flow_failure_rolls_back_both_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    original = PullCatalog._retain

    def interrupted(
        self: PullCatalog, name: str, document: object, encoded: bytes
    ) -> None:
        raise KeyboardInterrupt

    root = installation(tmp_path / "workstation")
    with PullCatalog(root) as catalog:
        catalog.enroll(endpoint(data))
        monkeypatch.setattr(PullCatalog, "_retain", interrupted)
        with pytest.raises(KeyboardInterrupt):
            catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0
        assert catalog.anchor("server").sequence == 0
        assert not catalog._db.in_transaction
        monkeypatch.setattr(PullCatalog, "_retain", original)
    with PullCatalog(root) as reopened:
        assert reopened.count() == 0
        reopened.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert reopened.anchor("server").sequence == 1


def test_restore_sql_failure_is_atomic(tmp_path: Path) -> None:
    data, _ = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "source")) as source:
        source.enroll(endpoint(data), data)
        backup = source.backup()
    with PullCatalog(installation(tmp_path / "target")) as target:
        before = target.backup()

        def reject_history(action: int, table: str | None, *_: object) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_INSERT and table == "pull_manifests"
                else sqlite3.SQLITE_OK
            )

        target._db.set_authorizer(reject_history)
        with pytest.raises(PullError):
            target.restore(backup)
        target._db.set_authorizer(None)
        assert target.backup() == before
        assert target.count() == 0
        target.restore(backup)
        assert target.backup() == backup


def test_failed_rollback_closes_registration_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    with PullCatalog(root) as catalog:
        catalog.enroll(endpoint(data))
        connection = catalog._db

        class BrokenRollback:
            def execute(
                self, sql: str, parameters: tuple[object, ...] = ()
            ) -> sqlite3.Cursor:
                if sql == "ROLLBACK":
                    raise sqlite3.OperationalError("rollback failure")
                return connection.execute(sql, parameters)

        def failed_registration(
            self: PullCatalog, name: str, document: object, encoded: bytes
        ) -> None:
            raise sqlite3.OperationalError("registration failure")

        catalog._db = BrokenRollback()  # type: ignore[assignment]
        monkeypatch.setattr(PullCatalog, "_retain", failed_registration)
        with pytest.raises(ImportError):
            catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        with pytest.raises(ImportError):
            catalog.count()
    with PullCatalog(root) as reopened:
        assert reopened.count() == 0
        assert reopened.anchor("server").sequence == 0


def test_history_quota_rolls_back_import_and_keeps_exact_backup_budget(
    tmp_path: Path,
) -> None:
    (first, second), incoming = published_chain(tmp_path)
    with PullCatalog(
        installation(tmp_path / "workstation"), pull_limits=PullLimits(max_manifests=1)
    ) as catalog:
        catalog.enroll(endpoint(first))
        catalog.accept_next("server", first, incoming, DEFAULT_RECEIPT)
        before = catalog.backup()
        with pytest.raises(ImportError):
            catalog.accept_next("server", second, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 1
        assert catalog.anchor("server").sequence == 1
        assert catalog.backup() == before
        catalog.pull_limits = replace(catalog.pull_limits, max_backup_bytes=len(before))
        assert catalog.backup() == before
        catalog.pull_limits = replace(
            catalog.pull_limits, max_backup_bytes=len(before) - 1
        )
        with pytest.raises(PullError):
            catalog.backup()


def test_exact_retained_replay_still_requires_f16_validation(tmp_path: Path) -> None:
    data, incoming = published_inputs(tmp_path)
    descriptor = parse_request(data, ImportLimits()).body.objects[0]
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data), data)
        before = catalog.backup()
        (incoming / descriptor.sha256).write_bytes(b"corrupted retained input")
        with pytest.raises(ImportError):
            catalog.accept_next("server", data, incoming, DEFAULT_RECEIPT)
        assert catalog.count() == 0
        assert catalog.backup() == before


def test_enrollment_checkpoint_identity_and_pin_conflicts(tmp_path: Path) -> None:
    data, _ = published_inputs(tmp_path)
    value = endpoint(data)
    different_epoch = replace(value, epoch="different")
    different_host = replace(value, host="127.0.0.2")
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        with pytest.raises(PullError):
            catalog.enroll(different_epoch, data)
        catalog.enroll(value)
        before = catalog.backup()
        catalog.enroll(value)
        with pytest.raises(PullError):
            catalog.enroll(different_host)
        with pytest.raises(PullError):
            catalog.enroll(value, data)
        assert catalog.backup() == before


def test_expired_deadline_rejects_before_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    called = False

    def unexpected_decode(self: LinuxDecoder, inputs: tuple[int, int, int]) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("decoder must not start")

    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))
        monkeypatch.setattr(LinuxDecoder, "decode", unexpected_decode)
        deadline = monotonic() - 1
        with pytest.raises(PullError):
            catalog.accept_next(
                "server", data, incoming, DEFAULT_RECEIPT, deadline=deadline
            )
        assert not called
        assert catalog.count() == 0
        assert catalog.anchor("server").sequence == 0


def test_decoder_finishing_after_deadline_cannot_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, incoming = published_inputs(tmp_path)
    original = LinuxDecoder.decode
    deadline = monotonic() + 1

    def slow_decode(self: LinuxDecoder, inputs: tuple[int, int, int]) -> bytes:
        result = original(self, inputs)
        sleep(max(0, deadline - monotonic()) + 0.01)
        return result

    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))
        monkeypatch.setattr(LinuxDecoder, "decode", slow_decode)
        with pytest.raises(ImportError):
            catalog.accept_next(
                "server", data, incoming, DEFAULT_RECEIPT, deadline=deadline
            )
        assert catalog.count() == 0
        assert catalog.anchor("server").sequence == 0
        assert catalog.history("server") == ()
        monkeypatch.setattr(LinuxDecoder, "decode", original)
        catalog.accept_next(
            "server", data, incoming, DEFAULT_RECEIPT, deadline=monotonic() + 5
        )
        assert catalog.count() == 1


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf"), True])
def test_invalid_deadline_rejected_without_progress(
    tmp_path: Path, deadline: float
) -> None:
    data, incoming = published_inputs(tmp_path)
    with PullCatalog(installation(tmp_path / "workstation")) as catalog:
        catalog.enroll(endpoint(data))
        with pytest.raises(PullError):
            catalog.accept_next(
                "server", data, incoming, DEFAULT_RECEIPT, deadline=deadline
            )
        assert catalog.count() == 0
        assert catalog.anchor("server").sequence == 0
