"""Bounded canonical primitive messages, independently checked by the coordinator."""

from dataclasses import dataclass

from scryntic.archive.canonical import JsonValue, canonical_json_bytes
from scryntic.archive.formats import (
    NORMALIZED_PARQUET_SCHEMA,
    PARQUET_CODEC,
    RAW_PARQUET_SCHEMA,
)
from scryntic.domain.validation import integer
from scryntic.publication.manifest import ManifestDocument, parse_manifest


class ImportError(RuntimeError):
    """Fixed-message import failure; never includes worker or remote content."""


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_manifest_bytes: int = 65536
    max_encoded_bytes: int = 32 * 1024 * 1024
    max_decoded_bytes: int = 64 * 1024 * 1024
    max_records: int = 10000
    max_message_bytes: int = 4096
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    cpu_seconds: int = 10
    wall_seconds: int = 20
    max_catalog_entries: int = 100000
    max_storage_bytes: int = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        for value in (
            self.max_manifest_bytes,
            self.max_encoded_bytes,
            self.max_decoded_bytes,
            self.max_records,
            self.max_message_bytes,
            self.memory_bytes,
            self.cpu_seconds,
            self.wall_seconds,
            self.max_catalog_entries,
            self.max_storage_bytes,
        ):
            integer(value, 1)
        if (
            self.max_manifest_bytes > 65536
            or self.max_message_bytes > 65536
            or self.max_encoded_bytes > 256 * 1024 * 1024
            or self.max_decoded_bytes > 256 * 1024 * 1024
            or self.max_records > 100000
            or self.memory_bytes > 4 * 1024**3
            or self.cpu_seconds > 60
            or self.wall_seconds > 120
        ):
            raise ValueError("Import policy exceeds qualified ceiling")


def parse_request(data: bytes, limits: ImportLimits) -> ManifestDocument:
    try:
        document = parse_manifest(data, limits.max_manifest_bytes)
        if document.body.record_count > limits.max_records:
            raise ValueError("Record limit")
        for descriptor, schema in zip(
            document.body.objects,
            (RAW_PARQUET_SCHEMA, NORMALIZED_PARQUET_SCHEMA),
            strict=True,
        ):
            if (
                descriptor.format != schema
                or descriptor.codec != PARQUET_CODEC
                or descriptor.encoded_bytes > limits.max_encoded_bytes
                or descriptor.decoded_bytes > limits.max_decoded_bytes
            ):
                raise ValueError("Unsupported or excessive descriptor")
        if len({item.sha256 for item in document.body.objects}) != 2:
            raise ValueError("Object role collision")
        return document
    except Exception:
        raise ImportError("Invalid import manifest") from None


def result_bytes(document: ManifestDocument) -> bytes:
    objects: list[JsonValue] = [
        {
            "role": item.role.value,
            "sha256": item.sha256,
            "encoded_bytes": item.encoded_bytes,
            "decoded_bytes": item.decoded_bytes,
            "record_count": item.record_count,
        }
        for item in document.body.objects
    ]
    return canonical_json_bytes(
        {
            "protocol": 1,
            "status": "valid",
            "manifest_hash": document.manifest_hash,
            "ordered_input_digest": document.body.ordered_input_digest,
            "objects": objects,
        }
    )


def validate_result(data: bytes, document: ManifestDocument) -> None:
    # Comparing the complete expected canonical primitive encoding also rejects
    # duplicate keys, booleans substituted for integers, extra fields and nesting.
    if type(data) is not bytes or data != result_bytes(document):
        raise ImportError("Invalid decoder result")
