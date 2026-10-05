"""Canonical v1 publication manifests and durable no-replace installation."""

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from scryntic.archive.canonical import (
    ORDERED_INPUT_ALGORITHM,
    JsonObject,
    JsonValue,
    canonical_json_bytes,
)
from scryntic.archive.model import ArchiveObject, ArchiveRole, Partition
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.identity import SchemaRef, Version
from scryntic.domain.raw import IngestionId
from scryntic.domain.validation import digest, identifier, integer

_IDENTITY_CONFLICT = "Manifest identity conflict"
_SEQUENCE_CONFLICT = "Manifest sequence conflict"

MANIFEST_SCHEMA = SchemaRef("scryntic.publication-manifest", Version(1, 0))
GENESIS_MANIFEST_HASH = "0" * 64
SERIALIZATION_ALGORITHM = "canonical-json-v1"
HASH_ALGORITHM = "sha256"
_SEQUENCE_WIDTH = 20
_INT64_MAX = 2**63 - 1


class ManifestError(RuntimeError):
    """Fixed-message manifest boundary failure."""


@dataclass(frozen=True, slots=True)
class ManifestRef:
    producer: str
    epoch: str
    sequence: int
    manifest_hash: str

    def __post_init__(self) -> None:
        identifier(self.producer)
        identifier(self.epoch)
        integer(self.sequence, 1)
        if self.sequence > _INT64_MAX:
            raise ValueError("Manifest sequence exceeds signed 64-bit range")
        digest(self.manifest_hash)


@dataclass(frozen=True, slots=True)
class ManifestBody:
    producer: str
    epoch: str
    sequence: int
    previous_manifest_hash: str
    checkpoint_before: IngestionId | None
    checkpoint_after: IngestionId
    first_ingestion: IngestionId
    last_ingestion: IngestionId
    record_count: int
    ordered_input_algorithm: str
    ordered_input_digest: str
    partition: Partition
    objects: tuple[ArchiveObject, ArchiveObject]
    schema: SchemaRef = field(default=MANIFEST_SCHEMA, init=False)
    serialization_algorithm: str = field(default=SERIALIZATION_ALGORITHM, init=False)
    hash_algorithm: str = field(default=HASH_ALGORITHM, init=False)

    def __post_init__(self) -> None:
        identifier(self.producer)
        identifier(self.epoch)
        integer(self.sequence, 1)
        integer(self.record_count, 1)
        if self.sequence > _INT64_MAX or self.record_count > _INT64_MAX:
            raise ValueError("Manifest integer exceeds signed 64-bit range")
        digest(self.previous_manifest_hash)
        digest(self.ordered_input_digest)
        if self.ordered_input_algorithm != ORDERED_INPUT_ALGORITHM:
            raise ValueError("Unsupported ordered input algorithm")
        if self.sequence == 1:
            if self.previous_manifest_hash != GENESIS_MANIFEST_HASH:
                raise ValueError("Invalid manifest genesis predecessor")
        elif self.previous_manifest_hash == GENESIS_MANIFEST_HASH:
            raise ValueError("Invalid manifest predecessor")
        if not isinstance(self.partition, Partition):
            raise TypeError("Expected a publication partition")
        self._validate_checkpoints()
        if type(self.objects) is not tuple or len(self.objects) != 2:
            raise ValueError("Manifest requires two archive objects")
        if tuple(value.role for value in self.objects) != (
            ArchiveRole.RAW,
            ArchiveRole.NORMALIZED,
        ):
            raise ValueError("Invalid manifest object role order")
        if any(value.record_count != self.record_count for value in self.objects):
            raise ValueError("Manifest object count mismatch")

    def _validate_checkpoints(self) -> None:
        identities = (self.checkpoint_after, self.first_ingestion, self.last_ingestion)
        if any(not isinstance(value, IngestionId) for value in identities):
            raise TypeError("Expected ingestion identities")
        if self.checkpoint_before is not None and not isinstance(
            self.checkpoint_before, IngestionId
        ):
            raise TypeError("Expected an ingestion checkpoint")
        if any(value.producer != self.producer for value in identities) or (
            self.checkpoint_before is not None
            and self.checkpoint_before.producer != self.producer
        ):
            raise ValueError("Manifest producer mismatch")
        if any(
            value.epoch != self.epoch
            for value in (self.first_ingestion, self.last_ingestion)
        ):
            raise ValueError("Manifest epoch mismatch")
        if not (
            self.first_ingestion.offset <= self.last_ingestion.offset
            and self.last_ingestion == self.checkpoint_after
        ):
            raise ValueError("Invalid manifest input coverage")
        if (
            self.checkpoint_before is not None
            and self.checkpoint_before.offset >= self.first_ingestion.offset
        ):
            raise ValueError("Manifest checkpoint did not advance")


@dataclass(frozen=True, slots=True)
class ManifestDocument:
    body: ManifestBody
    manifest_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, ManifestBody):
            raise TypeError("Expected a manifest body")
        digest(self.manifest_hash)

    @property
    def ref(self) -> ManifestRef:
        return ManifestRef(
            self.body.producer,
            self.body.epoch,
            self.body.sequence,
            self.manifest_hash,
        )


def _identity(value: IngestionId | None) -> JsonObject | None:
    if value is None:
        return None
    return {
        "producer": value.producer,
        "epoch": value.epoch,
        "offset": value.offset,
    }


def _schema(value: SchemaRef) -> JsonObject:
    return {
        "name": value.name,
        "major": value.version.major,
        "minor": value.version.minor,
    }


def _archive_object(value: ArchiveObject) -> JsonObject:
    return {
        "role": value.role.value,
        "sha256": value.sha256,
        "format": _schema(value.format),
        "codec": value.codec,
        "encoded_bytes": value.encoded_bytes,
        "decoded_bytes": value.decoded_bytes,
        "record_count": value.record_count,
    }


def _body_projection(value: ManifestBody) -> JsonObject:
    return {
        "schema": _schema(value.schema),
        "serialization_algorithm": value.serialization_algorithm,
        "hash_algorithm": value.hash_algorithm,
        "producer": value.producer,
        "epoch": value.epoch,
        "sequence": value.sequence,
        "previous_manifest_hash": value.previous_manifest_hash,
        "checkpoint_before": _identity(value.checkpoint_before),
        "checkpoint_after": _identity(value.checkpoint_after),
        "first_ingestion": _identity(value.first_ingestion),
        "last_ingestion": _identity(value.last_ingestion),
        "record_count": value.record_count,
        "ordered_input_algorithm": value.ordered_input_algorithm,
        "ordered_input_digest": value.ordered_input_digest,
        "partition": {
            "source": value.partition.source,
            "event_family": value.partition.event_family,
            "utc_date": value.partition.utc_date,
        },
        "objects": [_archive_object(item) for item in value.objects],
    }


def prepare_manifest(body: ManifestBody) -> bytes:
    if not isinstance(body, ManifestBody):
        raise TypeError("Expected a manifest body")
    body_projection = _body_projection(body)
    manifest_hash = sha256(canonical_json_bytes(body_projection)).hexdigest()
    return canonical_json_bytes(
        {"body": body_projection, "manifest_hash": manifest_hash}
    )


def _reject_constant(_: str) -> None:
    raise ValueError("Unsupported JSON constant")


def _reject_float(_: str) -> None:
    raise ValueError("Unsupported JSON float")


def _object_pairs(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _expect_object(value: JsonValue, keys: frozenset[str]) -> JsonObject:
    if type(value) is not dict or frozenset(value) != keys:
        raise ValueError("Invalid manifest fields")
    return value


def _text(value: JsonValue) -> str:
    if type(value) is not str:
        raise TypeError("Expected manifest text")
    return value


def _number(value: JsonValue, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _INT64_MAX:
        raise ValueError("Invalid manifest integer")
    return value


def _parse_schema(value: JsonValue) -> SchemaRef:
    item = _expect_object(value, frozenset(("name", "major", "minor")))
    return SchemaRef(
        _text(item["name"]),
        Version(_number(item["major"], 1), _number(item["minor"])),
    )


def _parse_identity(value: JsonValue, *, nullable: bool = False) -> IngestionId | None:
    if nullable and value is None:
        return None
    item = _expect_object(value, frozenset(("producer", "epoch", "offset")))
    return IngestionId(
        _text(item["producer"]),
        _text(item["epoch"]),
        _number(item["offset"]),
    )


def _parse_object(value: JsonValue) -> ArchiveObject:
    item = _expect_object(
        value,
        frozenset(
            (
                "role",
                "sha256",
                "format",
                "codec",
                "encoded_bytes",
                "decoded_bytes",
                "record_count",
            )
        ),
    )
    return ArchiveObject(
        ArchiveRole(_text(item["role"])),
        _text(item["sha256"]),
        _parse_schema(item["format"]),
        _text(item["codec"]),
        _number(item["encoded_bytes"], 1),
        _number(item["decoded_bytes"], 1),
        _number(item["record_count"], 1),
    )


def _parse_body(value: JsonValue) -> ManifestBody:
    keys = frozenset(
        (
            "schema",
            "serialization_algorithm",
            "hash_algorithm",
            "producer",
            "epoch",
            "sequence",
            "previous_manifest_hash",
            "checkpoint_before",
            "checkpoint_after",
            "first_ingestion",
            "last_ingestion",
            "record_count",
            "ordered_input_algorithm",
            "ordered_input_digest",
            "partition",
            "objects",
        )
    )
    item = _expect_object(value, keys)
    if _parse_schema(item["schema"]) != MANIFEST_SCHEMA:
        raise ValueError("Unsupported manifest schema")
    if _text(item["serialization_algorithm"]) != SERIALIZATION_ALGORITHM:
        raise ValueError("Unsupported manifest serialization")
    if _text(item["hash_algorithm"]) != HASH_ALGORITHM:
        raise ValueError("Unsupported manifest hash algorithm")
    partition_value = _expect_object(
        item["partition"], frozenset(("source", "event_family", "utc_date"))
    )
    objects_value = item["objects"]
    if type(objects_value) is not list or len(objects_value) != 2:
        raise ValueError("Invalid manifest objects")
    checkpoint_after = _parse_identity(item["checkpoint_after"])
    first_ingestion = _parse_identity(item["first_ingestion"])
    last_ingestion = _parse_identity(item["last_ingestion"])
    assert checkpoint_after is not None
    assert first_ingestion is not None
    assert last_ingestion is not None
    return ManifestBody(
        producer=_text(item["producer"]),
        epoch=_text(item["epoch"]),
        sequence=_number(item["sequence"], 1),
        previous_manifest_hash=_text(item["previous_manifest_hash"]),
        checkpoint_before=_parse_identity(item["checkpoint_before"], nullable=True),
        checkpoint_after=checkpoint_after,
        first_ingestion=first_ingestion,
        last_ingestion=last_ingestion,
        record_count=_number(item["record_count"], 1),
        ordered_input_algorithm=_text(item["ordered_input_algorithm"]),
        ordered_input_digest=_text(item["ordered_input_digest"]),
        partition=Partition(
            _text(partition_value["source"]),
            _text(partition_value["event_family"]),
            _text(partition_value["utc_date"]),
        ),
        objects=(_parse_object(objects_value[0]), _parse_object(objects_value[1])),
    )


def parse_manifest(data: bytes, max_bytes: int) -> ManifestDocument:
    try:
        integer(max_bytes, 1)
        if type(data) is not bytes or len(data) > max_bytes:
            raise ManifestError("Manifest limit exceeded")
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("Manifest BOM is forbidden")
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
        wrapper = _expect_object(value, frozenset(("body", "manifest_hash")))
        body = _parse_body(wrapper["body"])
        manifest_hash = _text(wrapper["manifest_hash"])
        digest(manifest_hash)
        expected = sha256(canonical_json_bytes(_body_projection(body))).hexdigest()
        if manifest_hash != expected:
            raise ValueError("Manifest body hash mismatch")
        document = ManifestDocument(body, manifest_hash)
        if prepare_manifest(body) != data:
            raise ValueError("Manifest is not canonical")
        return document
    except BaseException as error:
        if isinstance(error, ManifestError):
            raise
        if isinstance(error, Exception):
            raise ManifestError("Invalid publication manifest") from None
        raise


class ManifestStorage:
    """Install and resolve exact prepared manifest bytes under generated paths."""

    def __init__(
        self,
        installation: Installation,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self._owner_uid = installation.owner_uid
        self._fault = fault
        self._root = installation.state_dir / "archive"
        self._manifests = self._root / "manifests"
        self._staging = self._root / "manifest-staging"
        try:
            with directory(
                installation.state_dir, installation.owner_uid, private=True
            ):
                self._ensure_directory(self._root)
            self._ensure_directory(self._manifests)
            self._ensure_directory(self._staging)
        except BaseException as error:
            if isinstance(error, ManifestError):
                raise
            if isinstance(error, Exception):
                raise ManifestError("Unable to initialize manifest storage") from None
            raise

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ManifestError("Unsafe manifest directory")
        # Existence alone does not establish durability after an interrupted mkdir.
        self._notify("before_manifest_parent_directory_fsync")
        self._fsync_directory(path.parent)
        self._notify("after_manifest_parent_directory_fsync")

    def _notify(self, stage: str) -> None:
        if self._fault is not None:
            self._fault(stage)

    @contextmanager
    def _sequence_lock(self, path: Path) -> Iterator[None]:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ManifestError("Publication sequence is already owned") from None
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _epoch_digest(producer: str, epoch: str) -> str:
        return sha256(
            canonical_json_bytes({"epoch": epoch, "producer": producer})
        ).hexdigest()

    def _sequence_directory(self, ref: ManifestRef) -> Path:
        return (
            self._manifests
            / self._epoch_digest(ref.producer, ref.epoch)
            / f"{ref.sequence:0{_SEQUENCE_WIDTH}d}"
        )

    def manifest_path(self, ref: ManifestRef) -> Path:
        if not isinstance(ref, ManifestRef):
            raise TypeError("Expected a manifest reference")
        return self._sequence_directory(ref) / f"{ref.manifest_hash}.json"

    def _ensure_sequence_directory(self, ref: ManifestRef) -> Path:
        current = self._manifests
        for component in (
            self._sequence_directory(ref).relative_to(self._manifests).parts
        ):
            current = current / component
            self._ensure_directory(current)
            self._notify("before_manifest_parent_directory_fsync")
            self._fsync_directory(current.parent)
            self._notify("after_manifest_parent_directory_fsync")
        return current

    @staticmethod
    def _entries(path: Path) -> tuple[str, ...]:
        entries: list[str] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                entries.append(entry.name)
                if len(entries) == 2:
                    break
        return tuple(entries)

    def _read_file(self, path: Path, max_bytes: int) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._owner_uid
                or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_size > max_bytes
            ):
                raise ManifestError("Unsafe publication manifest")
            data = bytearray()
            while len(data) <= max_bytes:
                chunk = os.read(fd, min(8192, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > max_bytes:
                raise ManifestError("Manifest limit exceeded")
            return bytes(data)
        finally:
            os.close(fd)

    def install_exact(self, data: bytes, ref: ManifestRef) -> None:
        try:
            document = parse_manifest(data, len(data))
            if document.ref != ref:
                raise ManifestError(_IDENTITY_CONFLICT)
            sequence_dir = self._ensure_sequence_directory(ref)
            with self._sequence_lock(sequence_dir):
                self._install_locked(data, ref, sequence_dir)
        except BaseException as error:
            if isinstance(error, ManifestError):
                raise
            if isinstance(error, Exception):
                raise ManifestError("Unable to install publication manifest") from None
            raise

    def _install_locked(
        self, data: bytes, ref: ManifestRef, sequence_dir: Path
    ) -> None:
        try:
            target = self.manifest_path(ref)
            entries = self._entries(sequence_dir)
            if entries and entries != (target.name,):
                raise ManifestError(_SEQUENCE_CONFLICT)
            fd, name = tempfile.mkstemp(
                prefix=".manifest-", suffix=".json", dir=self._staging
            )
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(data)
                    stream.flush()
                    self._notify("before_manifest_file_fsync")
                    os.fsync(stream.fileno())
                if self._fault is not None:
                    self._fault("after_manifest_file_fsync")
                staging.chmod(0o400, follow_symlinks=False)
                readonly_fd = os.open(
                    staging, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                )
                try:
                    self._notify("before_manifest_readonly_fsync")
                    os.fsync(readonly_fd)
                    self._notify("after_manifest_readonly_fsync")
                finally:
                    os.close(readonly_fd)
                try:
                    self._notify("before_manifest_install")
                    os.link(staging, target, follow_symlinks=False)
                except FileExistsError:
                    if self._read_file(target, len(data)) != data:
                        raise ManifestError(_IDENTITY_CONFLICT) from None
                if self._fault is not None:
                    self._fault("after_manifest_install")
                self._notify("before_manifest_directory_fsync")
                self._fsync_directory(sequence_dir)
                if self._fault is not None:
                    self._fault("after_manifest_directory_fsync")
            finally:
                if staging.exists():
                    staging.unlink()
                self._notify("before_manifest_staging_directory_fsync")
                self._fsync_directory(self._staging)
                self._notify("after_manifest_staging_directory_fsync")
        except BaseException as error:
            if isinstance(error, ManifestError):
                raise
            if isinstance(error, Exception):
                raise ManifestError("Unable to install publication manifest") from None
            raise

    def read_exact(self, ref: ManifestRef, max_bytes: int) -> bytes:
        try:
            integer(max_bytes, 1)
            sequence_dir = self._sequence_directory(ref)
            entries = self._entries(sequence_dir)
            if len(entries) != 1 or entries[0] != f"{ref.manifest_hash}.json":
                raise ManifestError(_SEQUENCE_CONFLICT)
            data = self._read_file(self.manifest_path(ref), max_bytes)
            if parse_manifest(data, max_bytes).ref != ref:
                raise ManifestError(_IDENTITY_CONFLICT)
            return data
        except BaseException as error:
            if isinstance(error, ManifestError):
                raise
            if isinstance(error, Exception):
                raise ManifestError("Unable to read publication manifest") from None
            raise

    def _committed_slot(
        self, sequence: os.DirEntry[str], max_bytes: int
    ) -> tuple[ManifestDocument, bytes] | None:
        if (
            len(sequence.name) != _SEQUENCE_WIDTH
            or not sequence.name.isascii()
            or not sequence.name.isdecimal()
            or int(sequence.name) < 1
        ):
            raise ManifestError("Invalid committed manifest slot")
        path = Path(sequence.path)
        self._validate_directory(path)
        names = self._entries(path)
        if not names:
            return None
        if len(names) != 1:
            raise ManifestError(_SEQUENCE_CONFLICT)
        data = self._read_file(path / names[0], max_bytes)
        document = parse_manifest(data, max_bytes)
        if self.manifest_path(document.ref) != path / names[0]:
            raise ManifestError("Manifest namespace conflict")
        return document, data

    def iter_committed(
        self, max_bytes: int
    ) -> Iterator[tuple[ManifestDocument, bytes]]:
        """Walk only committed manifests; empty crash-created slots are harmless."""
        try:
            with os.scandir(self._manifests) as epochs:
                for epoch in epochs:
                    digest(epoch.name)
                    epoch_path = Path(epoch.path)
                    self._validate_directory(epoch_path)
                    with os.scandir(epoch_path) as sequences:
                        for sequence in sequences:
                            committed = self._committed_slot(sequence, max_bytes)
                            if committed is not None:
                                yield committed
        except BaseException as error:
            if isinstance(error, ManifestError):
                raise
            if isinstance(error, Exception):
                raise ManifestError("Unable to reconcile committed manifests") from None
            raise

    def _validate_directory(self, path: Path) -> None:
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ManifestError("Unsafe manifest directory")
