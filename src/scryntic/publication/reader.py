"""Bounded canonical manifest and referenced-object validation."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from scryntic.application.archive import RawRecordRef
from scryntic.archive.canonical import (
    PublicationInput,
    canonical_json_bytes,
    ordered_input_digest,
    raw_projection,
)
from scryntic.archive.model import ArchiveRole
from scryntic.archive.normalized_parquet import (
    NormalizedParquetArchive,
    archived_normalization,
)
from scryntic.archive.raw_parquet import (
    PARQUET_CODEC,
    RAW_PARQUET_SCHEMA,
    ParquetRawArchive,
)
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.domain.validation import digest, integer
from scryntic.publication.manifest import (
    GENESIS_MANIFEST_HASH,
    ManifestDocument,
    ManifestError,
    ManifestRef,
    ManifestStorage,
    parse_manifest,
)
from scryntic.publication.sqlite_store import (
    CatalogEntry,
    PublicationLimits,
    PublicationStore,
)


class PublicationReaderError(RuntimeError):
    """Fixed-message publication read failure."""


class Continuity(StrEnum):
    SELF = "self"
    EPOCH = "epoch"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class ValidatedManifest:
    document: ManifestDocument
    raw_records: tuple[RawRecord, ...]
    continuity: Continuity


class PublicationReader:
    def __init__(
        self,
        store: PublicationStore,
        installation: Installation,
        limits: PublicationLimits,
    ) -> None:
        if not isinstance(store, PublicationStore) or not isinstance(
            limits, PublicationLimits
        ):
            raise TypeError("Expected publication store and limits")
        self._store = store
        self._limits = limits
        self._manifests = ManifestStorage(installation)
        self._raw = ParquetRawArchive(installation)
        self._normalized = NormalizedParquetArchive(installation)
        self._storage = ImmutableArchiveStorage(installation)

    def _external_document(self, ref: ManifestRef) -> tuple[ManifestDocument, bytes]:
        try:
            data = self._manifests.read_exact(ref, self._limits.max_manifest_bytes)
            document = parse_manifest(data, self._limits.max_manifest_bytes)
            if document.ref != ref:
                raise PublicationReaderError("Publication manifest identity mismatch")
            return document, data
        except ManifestError:
            raise PublicationReaderError("Invalid publication manifest") from None

    def _validate_catalog(
        self, document: ManifestDocument, data: bytes
    ) -> CatalogEntry | None:
        entry = self._store.catalog_by_hash(document.manifest_hash)
        if entry is None:
            return None
        if (
            entry.ref != document.ref
            or entry.manifest_bytes != data
            or entry.checkpoint_before != document.body.checkpoint_before
            or entry.checkpoint_after != document.body.checkpoint_after
            or entry.previous_manifest_hash != document.body.previous_manifest_hash
            or entry.ordered_input_digest != document.body.ordered_input_digest
        ):
            raise PublicationReaderError("Publication catalog disagrees with manifest")
        return entry

    def _continuity(self, document: ManifestDocument) -> Continuity:
        body = document.body
        continuity = Continuity.SELF
        if body.sequence == 1:
            if body.previous_manifest_hash != GENESIS_MANIFEST_HASH:
                raise PublicationReaderError("Invalid manifest predecessor")
        else:
            predecessor = ManifestRef(
                body.producer,
                body.epoch,
                body.sequence - 1,
                body.previous_manifest_hash,
            )
            try:
                previous, previous_bytes = self._external_document(predecessor)
            except PublicationReaderError:
                raise PublicationReaderError("Invalid manifest predecessor") from None
            if (
                previous.ref != predecessor
                or previous.body.checkpoint_after.offset >= body.checkpoint_after.offset
            ):
                raise PublicationReaderError("Invalid manifest predecessor")
            catalog = self._store.catalog_by_epoch_sequence(
                body.epoch, body.sequence - 1
            )
            if catalog is not None and (
                catalog.ref != predecessor or catalog.manifest_bytes != previous_bytes
            ):
                raise PublicationReaderError("Invalid manifest predecessor")
            continuity = Continuity.EPOCH
        if body.checkpoint_before is not None:
            global_predecessor = self._store.catalog_by_checkpoint(
                body.checkpoint_before
            )
            if global_predecessor is not None:
                previous, previous_bytes = self._external_document(
                    global_predecessor.ref
                )
                if (
                    previous.ref != global_predecessor.ref
                    or previous_bytes != global_predecessor.manifest_bytes
                    or previous.body.checkpoint_after != body.checkpoint_before
                ):
                    raise PublicationReaderError(
                        "Invalid global publication predecessor"
                    )
                continuity = Continuity.GLOBAL
        return continuity

    def _read_objects(self, document: ManifestDocument) -> tuple[RawRecord, ...]:
        body = document.body
        raw_descriptor, normalized_descriptor = body.objects
        if (
            raw_descriptor.role is not ArchiveRole.RAW
            or raw_descriptor.format != RAW_PARQUET_SCHEMA
            or raw_descriptor.codec != PARQUET_CODEC
            or raw_descriptor.record_count != body.record_count
            or normalized_descriptor.role is not ArchiveRole.NORMALIZED
            or normalized_descriptor.record_count != body.record_count
        ):
            raise PublicationReaderError("Invalid publication object descriptor")
        raw_path = self._storage.object_path(raw_descriptor.sha256)
        try:
            if (
                raw_path.stat(follow_symlinks=False).st_size
                != raw_descriptor.encoded_bytes
            ):
                raise PublicationReaderError("Invalid publication object descriptor")
            normalized = asyncio.run(
                self._normalized.read(normalized_descriptor, self._limits.normalized)
            )
            identities = tuple(item.identity for item in normalized)
            records = tuple(
                asyncio.run(
                    self._raw.read(
                        RawRecordRef(raw_descriptor.sha256, index, identity),
                        self._limits.raw,
                    )
                )
                for index, identity in enumerate(identities)
            )
        except PublicationReaderError:
            raise
        except Exception:
            raise PublicationReaderError("Invalid publication object") from None
        if len(normalized) != len(records):
            raise PublicationReaderError("Publication object rows disagree")
        if (
            sum(
                len(canonical_json_bytes({"raw": raw_projection(record)}))
                for record in records
            )
            != raw_descriptor.decoded_bytes
        ):
            raise PublicationReaderError("Invalid publication object descriptor")
        values: list[PublicationInput] = []
        for record, archived in zip(records, normalized, strict=True):
            if archived.identity != record.identity:
                raise PublicationReaderError("Publication object rows disagree")
            try:
                value = PublicationInput(record, archived.outcome, archived.semantics)
            except (TypeError, ValueError):
                raise PublicationReaderError(
                    "Publication object rows disagree"
                ) from None
            if archived != archived_normalization(value):
                raise PublicationReaderError("Publication object rows disagree")
            values.append(value)
        if ordered_input_digest(tuple(values)) != body.ordered_input_digest:
            raise PublicationReaderError("Publication ordered input digest mismatch")
        if any(
            record.identity.epoch != body.epoch
            or record.envelope.source != body.partition.source
            or self._utc_date(record) != body.partition.utc_date
            for record in records
        ):
            raise PublicationReaderError("Publication partition mismatch")
        return records

    @staticmethod
    def _utc_date(record: RawRecord) -> str:
        try:
            seconds = record.envelope.receipt.wall_time_ns // 1_000_000_000
            return datetime.fromtimestamp(seconds, UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            raise PublicationReaderError("Invalid publication partition") from None

    def resolve_exact(self, reference: ManifestRef | str) -> ValidatedManifest:
        try:
            if isinstance(reference, str):
                digest(reference)
                catalog = self._store.catalog_by_hash(reference)
                if catalog is None:
                    raise PublicationReaderError("Unknown publication manifest hash")
                ref = catalog.ref
            elif isinstance(reference, ManifestRef):
                ref = reference
            else:
                raise TypeError("Expected manifest identity or hash")
            document, data = self._external_document(ref)
            self._validate_catalog(document, data)
            continuity = self._continuity(document)
            records = self._read_objects(document)
            body = document.body
            if (
                len(records) != body.record_count
                or records[0].identity != body.first_ingestion
                or records[-1].identity != body.last_ingestion
                or records[-1].identity != body.checkpoint_after
                or (
                    body.checkpoint_before is not None
                    and body.checkpoint_before.offset >= records[0].identity.offset
                )
            ):
                raise PublicationReaderError("Invalid publication input coverage")
            return ValidatedManifest(document, records, continuity)
        except PublicationReaderError:
            raise
        except Exception:
            raise PublicationReaderError("Unable to validate publication") from None

    def catalog_page(
        self, anchor: IngestionId | None, limit: int
    ) -> tuple[ValidatedManifest, ...]:
        integer(limit, 1)
        if limit > self._limits.max_manifests_per_read:
            raise PublicationReaderError("Publication page limit exceeded")
        if anchor is not None and self._store.catalog_by_checkpoint(anchor) is None:
            raise PublicationReaderError("Unknown publication page anchor")
        entries = self._store.catalog_page(anchor, limit)
        results: list[ValidatedManifest] = []
        expected_before = anchor
        for entry in entries:
            validated = self.resolve_exact(entry.ref)
            if validated.document.body.checkpoint_before != expected_before:
                raise PublicationReaderError("Invalid global publication continuity")
            results.append(validated)
            expected_before = validated.document.body.checkpoint_after
        return tuple(results)
