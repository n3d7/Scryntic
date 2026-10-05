"""F07 batch selection, durable publication ordering, and restart recovery."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from scryntic.application.archive import RawArchive, RawRecordRef, RawSegment
from scryntic.archive.canonical import (
    INPUT_FINGERPRINT_ALGORITHM,
    PublicationInput,
    canonical_json_bytes,
    input_fingerprint,
    normalized_projection,
    ordered_input_digest,
    raw_projection,
    semantics_projection,
)
from scryntic.archive.model import ArchiveObject, ArchiveRole, Partition
from scryntic.archive.normalized_parquet import (
    NORMALIZED_PARQUET_SCHEMA,
    NormalizedParquetArchive,
    archived_normalization,
)
from scryntic.archive.raw_parquet import PARQUET_CODEC, RAW_PARQUET_SCHEMA
from scryntic.archive.storage import ImmutableArchiveStorage
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import IngestionId, RawRecord
from scryntic.normalization.candle import CandleSemantics
from scryntic.normalization.runner import RawRecordReader
from scryntic.normalization.sqlite_store import OutcomeKind, ProcessingOutcome
from scryntic.publication.manifest import (
    ManifestBody,
    ManifestDocument,
    ManifestRef,
    ManifestStorage,
    parse_manifest,
    prepare_manifest,
)
from scryntic.publication.sqlite_store import (
    PendingPublication,
    PendingState,
    PublicationError,
    PublicationLimits,
    PublicationReservation,
    PublicationStore,
)

_INVALID_NORMALIZATION_RESULT = "Invalid normalization reader result"
_RESERVATION_EVIDENCE_CHANGED = "Publication reservation evidence changed"


class NormalizationReader(Protocol):
    def outcome(self, identity: IngestionId) -> ProcessingOutcome | None: ...

    def observation(self, revision: str) -> CandleSemantics | None: ...


@dataclass(frozen=True, slots=True)
class NoPublishableWork:
    checkpoint: IngestionId | None


@dataclass(frozen=True, slots=True)
class WaitingForNormalization:
    checkpoint: IngestionId | None
    blocker: IngestionId


@dataclass(frozen=True, slots=True)
class Published:
    manifest: ManifestRef
    checkpoint: IngestionId
    document: ManifestDocument
    document_bytes: bytes


@dataclass(frozen=True, slots=True)
class NoRecovery:
    checkpoint: IngestionId | None


@dataclass(frozen=True, slots=True)
class Recovered:
    publication: Published


type PublishResult = Published | NoPublishableWork | WaitingForNormalization
type RecoveryResult = Recovered | NoRecovery


class PublicationCoordinator:
    """Publish only committed public F05/F06 values in recoverable order."""

    def __init__(
        self,
        raw_reader: RawRecordReader,
        normalization_reader: NormalizationReader,
        store: PublicationStore,
        raw_archive: RawArchive,
        normalized_archive: NormalizedParquetArchive,
        manifests: ManifestStorage,
        installation: Installation,
        limits: PublicationLimits,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(store, PublicationStore) or not isinstance(
            limits, PublicationLimits
        ):
            raise TypeError("Expected publication store and limits")
        self._raw_reader = raw_reader
        self._normalization_reader = normalization_reader
        self._store = store
        self._raw_archive = raw_archive
        self._normalized_archive = normalized_archive
        self._manifests = manifests
        self._storage = ImmutableArchiveStorage(
            installation,
            fault=None if fault is None else lambda stage: fault(f"views_{stage}"),
        )
        self._limits = limits
        self._fault_callback = fault
        self._history_checked = False

    def _fault(self, stage: str) -> None:
        if self._fault_callback is not None:
            self._fault_callback(stage)

    def _read_raw(self, offset: int, limit: int) -> tuple[RawRecord, ...]:
        try:
            records = self._raw_reader.records_after(offset, limit=limit)
        except Exception:
            raise PublicationError("Unable to read publication raw records") from None
        if not isinstance(records, tuple) or len(records) > limit:
            raise PublicationError("Invalid publication reader result")
        previous = offset
        producer = self._store.status().producer
        for record in records:
            if (
                not isinstance(record, RawRecord)
                or record.identity.producer != producer
                or record.identity.offset <= previous
            ):
                raise PublicationError("Invalid publication reader result")
            previous = record.identity.offset
        return records

    def _publication_input(self, record: RawRecord) -> PublicationInput | None:
        try:
            outcome = self._normalization_reader.outcome(record.identity)
        except Exception:
            raise PublicationError("Unable to read normalization outcome") from None
        if outcome is None:
            return None
        if not isinstance(outcome, ProcessingOutcome):
            raise PublicationError(_INVALID_NORMALIZATION_RESULT)
        semantics: CandleSemantics | None = None
        if outcome.kind is not OutcomeKind.REJECTED:
            if outcome.semantic_revision is None:
                raise PublicationError(_INVALID_NORMALIZATION_RESULT)
            try:
                semantics = self._normalization_reader.observation(
                    outcome.semantic_revision
                )
            except Exception:
                raise PublicationError(
                    "Unable to read normalization observation"
                ) from None
            if not isinstance(semantics, CandleSemantics):
                raise PublicationError(_INVALID_NORMALIZATION_RESULT)
        try:
            return PublicationInput(record, outcome, semantics)
        except (TypeError, ValueError):
            raise PublicationError(_INVALID_NORMALIZATION_RESULT) from None

    @staticmethod
    def _partition(record: RawRecord) -> Partition:
        try:
            seconds = record.envelope.receipt.wall_time_ns // 1_000_000_000
            utc_date = datetime.fromtimestamp(seconds, UTC).date().isoformat()
            return Partition(record.envelope.source, "candle", utc_date)
        except (OverflowError, OSError, ValueError):
            raise PublicationError("Invalid publication partition time") from None

    @staticmethod
    def _logical_sizes(value: PublicationInput) -> tuple[int, int]:
        return (
            len(canonical_json_bytes({"raw": raw_projection(value.raw)})),
            len(
                canonical_json_bytes(
                    {
                        "outcome": normalized_projection(value.outcome),
                        "semantics": semantics_projection(value.semantics),
                    }
                )
            ),
        )

    def _batch_fits(self, count: int, raw_bytes: int, normalized_bytes: int) -> bool:
        return (
            count <= self._limits.max_batch_records
            and count <= self._limits.raw.max_records
            and count <= self._limits.normalized.max_records
            and raw_bytes <= self._limits.raw.max_decoded_bytes
            and normalized_bytes <= self._limits.normalized.max_decoded_bytes
        )

    def _admit_batch_record(
        self, count: int, raw_bytes: int, normalized_bytes: int
    ) -> bool:
        if self._batch_fits(count, raw_bytes, normalized_bytes):
            return True
        if count == 1:
            raise PublicationError("Publication limits reject next record")
        return False

    def _select(
        self,
    ) -> tuple[PublicationInput, ...] | WaitingForNormalization | NoPublishableWork:
        status = self._store.status()
        offset = 0 if status.checkpoint is None else status.checkpoint.offset
        records = self._read_raw(offset, self._limits.max_batch_records)
        if not records:
            return NoPublishableWork(status.checkpoint)
        selected: list[PublicationInput] = []
        partition: Partition | None = None
        epoch: str | None = None
        raw_bytes = 0
        normalized_bytes = 0
        for record in records:
            value = self._publication_input(record)
            if value is None:
                if not selected:
                    return WaitingForNormalization(status.checkpoint, record.identity)
                break
            current_partition = self._partition(record)
            if selected and (
                record.identity.epoch != epoch or current_partition != partition
            ):
                break
            raw_size, normalized_size = self._logical_sizes(value)
            if not self._admit_batch_record(
                len(selected) + 1,
                raw_bytes + raw_size,
                normalized_bytes + normalized_size,
            ):
                break
            selected.append(value)
            epoch = record.identity.epoch
            partition = current_partition
            raw_bytes += raw_size
            normalized_bytes += normalized_size
        return tuple(selected)

    def _reserve(self, values: tuple[PublicationInput, ...]) -> PendingPublication:
        status = self._store.status()
        epoch = values[0].raw.identity.epoch
        sequence, previous_hash = self._store.next_epoch_link(epoch)
        pairs = tuple(
            (value.raw.identity, input_fingerprint(value)) for value in values
        )
        reservation = PublicationReservation(
            status.checkpoint,
            values[-1].raw.identity,
            epoch,
            sequence,
            previous_hash,
            self._partition(values[0].raw),
            INPUT_FINGERPRINT_ALGORITHM,
            pairs,
            "scryntic-publication-ordered-input-v1",
            ordered_input_digest(values),
        )
        pending = self._store.reserve(reservation)
        self._fault("after_reservation_commit")
        return pending

    def _reread(
        self, reservation: PublicationReservation
    ) -> tuple[PublicationInput, ...]:
        offset = (
            0
            if reservation.checkpoint_before is None
            else reservation.checkpoint_before.offset
        )
        records = self._read_raw(offset, len(reservation.inputs))
        if tuple(record.identity for record in records) != tuple(
            identity for identity, _ in reservation.inputs
        ):
            raise PublicationError(_RESERVATION_EVIDENCE_CHANGED)
        values: list[PublicationInput] = []
        for record, (_, expected_fingerprint) in zip(
            records, reservation.inputs, strict=True
        ):
            try:
                value = self._publication_input(record)
            except PublicationError:
                raise PublicationError(_RESERVATION_EVIDENCE_CHANGED) from None
            if value is None or input_fingerprint(value) != expected_fingerprint:
                raise PublicationError(_RESERVATION_EVIDENCE_CHANGED)
            values.append(value)
        return tuple(values)

    @staticmethod
    def _raw_descriptor(segment: RawSegment) -> ArchiveObject:
        return ArchiveObject(
            ArchiveRole.RAW,
            segment.sha256,
            segment.format,
            segment.codec,
            segment.encoded_bytes,
            segment.decoded_bytes,
            segment.record_count,
        )

    def _seal_and_prepare(
        self, pending: PendingPublication, values: tuple[PublicationInput, ...]
    ) -> PendingPublication:
        raw_segment = asyncio.run(
            self._raw_archive.seal(
                tuple(value.raw for value in values), self._limits.raw
            )
        )
        if not isinstance(raw_segment, RawSegment):
            raise PublicationError("Invalid raw archive descriptor")
        raw_object = self._raw_descriptor(raw_segment)
        self._fault("after_raw_object")
        normalized_object = asyncio.run(
            self._normalized_archive.seal(values, self._limits.normalized)
        )
        if not isinstance(normalized_object, ArchiveObject):
            raise PublicationError("Invalid normalized archive descriptor")
        self._fault("after_normalized_object")
        partition = pending.reservation.partition
        self._storage.install_partition_view(
            ArchiveRole.RAW, partition, raw_object.sha256
        )
        self._storage.install_partition_view(
            ArchiveRole.NORMALIZED, partition, normalized_object.sha256
        )
        self._fault("after_partition_views")
        reservation = pending.reservation
        body = ManifestBody(
            producer=reservation.checkpoint_after.producer,
            epoch=reservation.epoch,
            sequence=reservation.sequence,
            previous_manifest_hash=reservation.previous_manifest_hash,
            checkpoint_before=reservation.checkpoint_before,
            checkpoint_after=reservation.checkpoint_after,
            first_ingestion=reservation.inputs[0][0],
            last_ingestion=reservation.inputs[-1][0],
            record_count=len(reservation.inputs),
            ordered_input_algorithm=reservation.ordered_input_algorithm,
            ordered_input_digest=reservation.ordered_input_digest,
            partition=reservation.partition,
            objects=(raw_object, normalized_object),
        )
        manifest_bytes = prepare_manifest(body)
        if len(manifest_bytes) > self._limits.max_manifest_bytes:
            raise PublicationError("Manifest limit exceeded")
        prepared = self._store.prepare(pending, manifest_bytes)
        self._fault("after_prepare_commit")
        return prepared

    def _validate_prepared_objects(
        self, pending: PendingPublication, values: tuple[PublicationInput, ...]
    ) -> ManifestDocument:
        if pending.manifest_bytes is None:
            raise PublicationError("Publication is not prepared")
        document = parse_manifest(
            pending.manifest_bytes, self._limits.max_manifest_bytes
        )
        raw_object, normalized_object = document.body.objects
        raw_bytes = sum(self._logical_sizes(value)[0] for value in values)
        try:
            encoded_bytes = (
                self._storage.object_path(raw_object.sha256)
                .stat(follow_symlinks=False)
                .st_size
            )
        except OSError:
            raise PublicationError("Prepared raw archive is unavailable") from None
        if (
            raw_object.format != RAW_PARQUET_SCHEMA
            or raw_object.codec != PARQUET_CODEC
            or raw_object.record_count != len(values)
            or raw_object.decoded_bytes != raw_bytes
            or raw_object.encoded_bytes > self._limits.raw.max_encoded_bytes
            or encoded_bytes != raw_object.encoded_bytes
        ):
            raise PublicationError("Prepared raw archive descriptor mismatch")
        for index, value in enumerate(values):
            record = asyncio.run(
                self._raw_archive.read(
                    RawRecordRef(raw_object.sha256, index, value.raw.identity),
                    self._limits.raw,
                )
            )
            if record != value.raw:
                raise PublicationError("Prepared raw archive evidence changed")
        normalized = asyncio.run(
            self._normalized_archive.read(normalized_object, self._limits.normalized)
        )
        if normalized != tuple(archived_normalization(value) for value in values):
            raise PublicationError("Prepared normalized archive evidence changed")
        for descriptor in document.body.objects:
            self._storage.install_partition_view(
                descriptor.role, document.body.partition, descriptor.sha256
            )
        return document

    def _install_and_commit(
        self, pending: PendingPublication, values: tuple[PublicationInput, ...]
    ) -> Published:
        document = self._validate_prepared_objects(pending, values)
        assert pending.manifest_bytes is not None
        self._manifests.install_exact(pending.manifest_bytes, document.ref)
        catalog = self._store.commit_prepared(pending)
        self._fault("after_catalog_commit")
        return Published(
            catalog.ref,
            catalog.checkpoint_after,
            document,
            pending.manifest_bytes,
        )

    def publish_next(self) -> PublishResult:
        self._reconcile_history()
        if self._store.pending() is not None:
            recovered = self.recover()
            if isinstance(recovered, Recovered):
                return recovered.publication
            raise PublicationError("Publication recovery made no progress")
        selected = self._select()
        if isinstance(selected, (NoPublishableWork, WaitingForNormalization)):
            return selected
        pending = self._reserve(selected)
        values = self._reread(pending.reservation)
        prepared = self._seal_and_prepare(pending, values)
        return self._install_and_commit(prepared, values)

    def recover(self) -> RecoveryResult:
        self._reconcile_history(force=True)
        pending = self._store.pending()
        if pending is None:
            return NoRecovery(self._store.status().checkpoint)
        values = self._reread(pending.reservation)
        if pending.state is PendingState.RESERVED:
            pending = self._seal_and_prepare(pending, values)
        return Recovered(self._install_and_commit(pending, values))

    def _reconcile_manifest(
        self,
        document: ManifestDocument,
        data: bytes,
        pending: PendingPublication | None,
    ) -> None:
        entry = self._store.catalog_by_epoch_sequence(
            document.ref.epoch, document.ref.sequence
        )
        if entry is not None:
            if entry.ref != document.ref or entry.manifest_bytes != data:
                raise PublicationError("Conflicting committed publication history")
        elif (
            pending is None
            or pending.state is not PendingState.PREPARED
            or pending.manifest_ref != document.ref
            or pending.manifest_bytes != data
        ):
            raise PublicationError("Unreconciled committed publication history")

    def _reconcile_history(self, *, force: bool = False) -> None:
        """Validate both authorities; never synthesize a missing reservation."""
        if self._history_checked and not force:
            checkpoint = self._store.status().checkpoint
            if checkpoint is not None:
                entry = self._store.catalog_by_checkpoint(checkpoint)
                if entry is None:
                    raise PublicationError("Missing committed publication history")
                self._validate_committed(entry.ref, entry.manifest_bytes)
            return
        # A failed full reconciliation must not leave an earlier successful cache.
        self._history_checked = False
        pending = self._store.pending()
        producer = self._store.status().producer
        for document, data in self._manifests.iter_committed(
            self._limits.max_manifest_bytes
        ):
            if document.ref.producer != producer:
                continue
            self._reconcile_manifest(document, data, pending)
        after: IngestionId | None = None
        while entries := self._store.catalog_page(
            after, self._limits.max_manifests_per_read
        ):
            for entry in entries:
                self._validate_committed(entry.ref, entry.manifest_bytes)
            after = entries[-1].checkpoint_after
        self._history_checked = True

    def _validate_committed(self, ref: ManifestRef, expected: bytes) -> None:
        data = self._manifests.read_exact(ref, self._limits.max_manifest_bytes)
        if data != expected:
            raise PublicationError("Conflicting committed publication history")
        document = parse_manifest(data, self._limits.max_manifest_bytes)
        for descriptor, schema, limits in zip(
            document.body.objects,
            (RAW_PARQUET_SCHEMA, NORMALIZED_PARQUET_SCHEMA),
            (self._limits.raw, self._limits.normalized),
            strict=True,
        ):
            try:
                path = self._storage.object_path(descriptor.sha256)
                if (
                    descriptor.format != schema
                    or descriptor.codec != PARQUET_CODEC
                    or descriptor.encoded_bytes > limits.max_encoded_bytes
                    or descriptor.decoded_bytes > limits.max_decoded_bytes
                    or descriptor.record_count > limits.max_records
                    or path.stat(follow_symlinks=False).st_size
                    != descriptor.encoded_bytes
                    or self._storage.file_sha256(path) != descriptor.sha256
                ):
                    raise PublicationError("Invalid committed publication object")
            except Exception:
                raise PublicationError("Invalid committed publication object") from None


__all__ = [
    "NoPublishableWork",
    "NoRecovery",
    "NormalizationReader",
    "PublicationCoordinator",
    "PublicationError",
    "Published",
    "Recovered",
    "WaitingForNormalization",
]
