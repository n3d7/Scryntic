"""F07 publication state ownership and atomic progress."""

from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.archive.canonical import (
    INPUT_FINGERPRINT_ALGORITHM,
    ORDERED_INPUT_ALGORITHM,
    ordered_digest_from_pairs,
)
from scryntic.domain.raw import IngestionId
from scryntic.publication.manifest import parse_manifest, prepare_manifest
from scryntic.publication.sqlite_store import (
    PublicationError,
    PublicationReservation,
    PublicationStore,
    PublisherOwned,
)
from tests.archive.helpers import installation
from tests.publication.test_manifest import body


def reservation(
    *,
    offset: int = 7,
    epoch: str = "epoch-a",
    sequence: int = 1,
    previous: str = "0" * 64,
    before: IngestionId | None = None,
) -> PublicationReservation:
    identity = IngestionId("producer-a", epoch, offset)
    inputs = ((identity, "a" * 64),)
    return PublicationReservation(
        checkpoint_before=before,
        checkpoint_after=identity,
        epoch=epoch,
        sequence=sequence,
        previous_manifest_hash=previous,
        partition=body(epoch=epoch, after_offset=offset).partition,
        input_fingerprint_algorithm=INPUT_FINGERPRINT_ALGORITHM,
        inputs=inputs,
        ordered_input_algorithm=ORDERED_INPUT_ALGORITHM,
        ordered_input_digest=ordered_digest_from_pairs(inputs),
    )


def prepared_bytes(value: PublicationReservation) -> bytes:
    manifest_body = body(
        epoch=value.epoch,
        sequence=value.sequence,
        previous=value.previous_manifest_hash,
        before=value.checkpoint_before,
        after_offset=value.checkpoint_after.offset,
    )
    return prepare_manifest(
        replace(
            manifest_body,
            ordered_input_digest=value.ordered_input_digest,
        )
    )


def test_catalog_checkpoint_and_pending_delete_commit_together(tmp_path: Path) -> None:
    with PublicationStore(installation(tmp_path), producer="producer-a") as store:
        pending = store.reserve(reservation())
        pending = store.prepare(pending, prepared_bytes(pending.reservation))
        catalog = store.commit_prepared(pending)

        assert store.status().checkpoint == IngestionId("producer-a", "epoch-a", 7)
        assert store.pending() is None
        assert store.catalog_by_hash(catalog.ref.manifest_hash) == catalog
        assert store.catalog_by_epoch_sequence("epoch-a", 1) == catalog
        assert store.catalog_by_checkpoint(catalog.checkpoint_after) == catalog


def test_offsets_may_have_gaps_and_epoch_chains_continue_independently(
    tmp_path: Path,
) -> None:
    with PublicationStore(installation(tmp_path), producer="producer-a") as store:
        checkpoint: IngestionId | None = None
        epoch_heads: dict[str, tuple[int, str]] = {}
        for offset, epoch in ((2, "epoch-a"), (5, "epoch-b"), (9, "epoch-a")):
            sequence, previous = epoch_heads.get(epoch, (0, "0" * 64))
            value = reservation(
                offset=offset,
                epoch=epoch,
                sequence=sequence + 1,
                previous=previous,
                before=checkpoint,
            )
            pending = store.reserve(value)
            pending = store.prepare(pending, prepared_bytes(value))
            catalog = store.commit_prepared(pending)
            checkpoint = catalog.checkpoint_after
            epoch_heads[epoch] = (catalog.ref.sequence, catalog.ref.manifest_hash)

        assert store.status().checkpoint == IngestionId("producer-a", "epoch-a", 9)
        assert store.next_epoch_link("epoch-a") == (3, epoch_heads["epoch-a"][1])
        assert store.next_epoch_link("epoch-b") == (2, epoch_heads["epoch-b"][1])
        assert [
            entry.checkpoint_after.offset for entry in store.catalog_page(None, 10)
        ] == [
            2,
            5,
            9,
        ]


def test_reservation_is_idempotent_but_immutable(tmp_path: Path) -> None:
    with PublicationStore(installation(tmp_path), producer="producer-a") as store:
        value = reservation(offset=3)
        assert store.reserve(value) == store.reserve(value)
        with pytest.raises(PublicationError, match="pending"):
            store.reserve(reservation(offset=4))


def test_store_lock_is_acquired_before_second_sqlite_owner(tmp_path: Path) -> None:
    first = PublicationStore(installation(tmp_path), producer="producer-a")
    try:
        with pytest.raises(PublisherOwned):
            PublicationStore(installation(tmp_path), producer="producer-a")
    finally:
        first.close()


def test_prepare_failure_rolls_back_exact_reserved_state(tmp_path: Path) -> None:
    stages: list[str] = []

    def fail(stage: str) -> None:
        stages.append(stage)
        if stage == "before_prepare_commit":
            raise OSError("injected")

    with PublicationStore(
        installation(tmp_path), producer="producer-a", fault=fail
    ) as store:
        pending = store.reserve(reservation())
        with pytest.raises(PublicationError):
            store.prepare(pending, prepared_bytes(pending.reservation))
        assert store.pending() == pending
        assert store.status().checkpoint is None
    assert "before_prepare_commit" in stages


@pytest.mark.parametrize(
    "failure_stage",
    ("after_catalog_insert", "after_checkpoint_update", "before_catalog_commit"),
)
def test_catalog_transaction_failure_never_advances_partial_progress(
    tmp_path: Path, failure_stage: str
) -> None:
    enabled = False

    def fail(stage: str) -> None:
        if enabled and stage == failure_stage:
            raise OSError("injected")

    with PublicationStore(
        installation(tmp_path), producer="producer-a", fault=fail
    ) as store:
        pending = store.reserve(reservation())
        pending = store.prepare(pending, prepared_bytes(pending.reservation))
        enabled = True
        with pytest.raises(PublicationError):
            store.commit_prepared(pending)
        assert store.status().checkpoint is None
        assert store.pending() == pending
        document = parse_manifest(pending.manifest_bytes or b"", 100_000)
        assert store.catalog_by_hash(document.manifest_hash) is None


def test_reopen_validates_pragmas_foreign_keys_and_producer(tmp_path: Path) -> None:
    root = installation(tmp_path)
    with PublicationStore(root, producer="producer-a") as store:
        status = store.status()
        assert status.journal_mode == "wal"
        assert status.synchronous == "FULL"
    with pytest.raises(PublicationError, match="producer"):
        PublicationStore(root, producer="other-producer")
