"""Canonical manifest bytes, validation, and durable installation."""

from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.archive.canonical import ORDERED_INPUT_ALGORITHM
from scryntic.archive.model import ArchiveObject, ArchiveRole, Partition
from scryntic.domain.identity import SchemaRef, Version
from scryntic.domain.raw import IngestionId
from scryntic.publication.manifest import (
    GENESIS_MANIFEST_HASH,
    MANIFEST_SCHEMA,
    ManifestBody,
    ManifestError,
    ManifestRef,
    ManifestStorage,
    parse_manifest,
    prepare_manifest,
)
from tests.archive.helpers import installation


def _object(role: ArchiveRole, digit: str) -> ArchiveObject:
    return ArchiveObject(
        role,
        digit * 64,
        SchemaRef(f"scryntic.{role.value}-record.parquet", Version(1, 0)),
        "parquet-pyarrow-v1",
        101,
        202,
        1,
    )


def body(
    *,
    epoch: str = "epoch-a",
    sequence: int = 1,
    previous: str = GENESIS_MANIFEST_HASH,
    before: IngestionId | None = None,
    after_offset: int = 7,
) -> ManifestBody:
    after = IngestionId("producer-a", epoch, after_offset)
    return ManifestBody(
        producer="producer-a",
        epoch=epoch,
        sequence=sequence,
        previous_manifest_hash=previous,
        checkpoint_before=before,
        checkpoint_after=after,
        first_ingestion=after,
        last_ingestion=after,
        record_count=1,
        ordered_input_algorithm=ORDERED_INPUT_ALGORITHM,
        ordered_input_digest="c" * 64,
        partition=Partition("fake", "candle", "2026-09-23"),
        objects=(_object(ArchiveRole.RAW, "a"), _object(ArchiveRole.NORMALIZED, "b")),
    )


def test_manifest_vector_fixes_body_wrapper_and_hash() -> None:
    data = prepare_manifest(body())

    assert data == (
        b'{"body":{"checkpoint_after":{"epoch":"epoch-a","offset":7,"producer":"producer-a"},'
        b'"checkpoint_before":null,"epoch":"epoch-a","first_ingestion":{"epoch":"epoch-a",'
        b'"offset":7,"producer":"producer-a"},"hash_algorithm":"sha256","last_ingestion":'
        b'{"epoch":"epoch-a","offset":7,"producer":"producer-a"},"objects":[{"codec":'
        b'"parquet-pyarrow-v1","decoded_bytes":202,"encoded_bytes":101,"format":{"major":1,'
        b'"minor":0,"name":"scryntic.raw-record.parquet"},"record_count":1,"role":"raw",'
        b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
        b'{"codec":"parquet-pyarrow-v1","decoded_bytes":202,"encoded_bytes":101,"format":'
        b'{"major":1,"minor":0,"name":"scryntic.normalized-record.parquet"},"record_count":1,'
        b'"role":"normalized","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],'
        b'"ordered_input_algorithm":"scryntic-publication-ordered-input-v1",'
        b'"ordered_input_digest":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        b'"partition":{"event_family":"candle","source":"fake","utc_date":"2026-09-23"},'
        b'"previous_manifest_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
        b'"producer":"producer-a","record_count":1,"schema":{"major":1,"minor":0,"name":'
        b'"scryntic.publication-manifest"},"sequence":1,"serialization_algorithm":"canonical-json-v1"},'
        b'"manifest_hash":"6d3d4ddcee84775784a7d263d2911095aa639a4b970dacb9138952dca97941ef"}'
    )
    parsed = parse_manifest(data, len(data))
    assert parsed.body == body()
    assert parsed.ref == ManifestRef("producer-a", "epoch-a", 1, parsed.manifest_hash)


@pytest.mark.parametrize(
    "mutated",
    (
        b"\xef\xbb\xbf{}",
        b'{"body":{},"body":{},"manifest_hash":"' + b"0" * 64 + b'"}',
        b'{"body":{},"manifest_hash":"' + b"A" * 64 + b'"}',
        b'{"body":{},"manifest_hash":"' + b"0" * 64 + b'"}\n',
    ),
)
def test_parser_rejects_noncanonical_or_duplicate_json(mutated: bytes) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(mutated, 100_000)


def test_manifest_enforces_chain_epoch_and_global_checkpoint_rules() -> None:
    first = body()
    first_document = parse_manifest(prepare_manifest(first), 100_000)
    earlier_epoch_checkpoint = IngestionId("producer-a", "epoch-a", 7)
    continued = body(
        sequence=2,
        previous=first_document.manifest_hash,
        before=earlier_epoch_checkpoint,
        after_offset=11,
    )
    continued_document = parse_manifest(prepare_manifest(continued), 100_000)
    assert continued_document.body == continued
    assert (
        continued_document.manifest_hash
        == "15185844cc99921d870d1b655f7c9a8b402f51a9678443343602f1fb4cc769a2"
    )
    epoch_b = body(
        epoch="epoch-b",
        before=earlier_epoch_checkpoint,
        after_offset=11,
    )
    assert parse_manifest(prepare_manifest(epoch_b), 100_000).body == epoch_b
    with pytest.raises((TypeError, ValueError)):
        replace(continued, checkpoint_after=IngestionId("producer-a", "epoch-a", 6))


def test_storage_installs_exact_bytes_without_replacement(tmp_path: Path) -> None:
    store = ManifestStorage(installation(tmp_path))
    data = prepare_manifest(body())
    document = parse_manifest(data, len(data))

    store.install_exact(data, document.ref)
    store.install_exact(data, document.ref)

    assert store.read_exact(document.ref, len(data)) == data
    assert store.manifest_path(document.ref).read_bytes() == data


def test_storage_rejects_conflicting_sequence_and_exact_identity(
    tmp_path: Path,
) -> None:
    store = ManifestStorage(installation(tmp_path))
    data = prepare_manifest(body())
    document = parse_manifest(data, len(data))
    store.install_exact(data, document.ref)

    conflicting = replace(document.ref, manifest_hash="d" * 64)
    with pytest.raises(ManifestError, match="conflict"):
        store.install_exact(data, conflicting)
    path = store.manifest_path(document.ref)
    path.chmod(0o600)
    path.write_bytes(b"different")
    path.chmod(0o400)
    with pytest.raises(ManifestError):
        store.read_exact(document.ref, 100_000)


def test_parser_enforces_limit_and_supported_schema() -> None:
    data = prepare_manifest(body())
    with pytest.raises(ManifestError, match="limit"):
        parse_manifest(data, len(data) - 1)
    assert parse_manifest(data, len(data)).body.schema == MANIFEST_SCHEMA
