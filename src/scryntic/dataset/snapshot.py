"""Immutable candle snapshots from exact validated F07 publications."""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scryntic.application.dto import BuildDatasetRequest, BuildDatasetResult
from scryntic.application.sources import BYBIT_CANDLE_SCHEMA
from scryntic.archive.canonical import (
    JsonValue,
    canonical_json_bytes,
    input_fingerprint,
)
from scryntic.archive.normalized_parquet import NORMALIZED_PARQUET_SCHEMA
from scryntic.archive.raw_parquet import RAW_PARQUET_SCHEMA
from scryntic.clock.policy import time_interval
from scryntic.configuration.clock import ClockLimits
from scryntic.configuration.paths import Installation
from scryntic.dataset.schemas import (
    AS_OBSERVED_CANDLE_RECIPE_SCHEMA as AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
)
from scryntic.dataset.schemas import (
    CANDLE_RECIPE_SCHEMA as CANDLE_RECIPE_SCHEMA,
)
from scryntic.dataset.schemas import (
    DATASET_SCHEMA as DATASET_SCHEMA,
)
from scryntic.dataset.schemas import (
    MANIFEST_SCHEMA as MANIFEST_SCHEMA,
)
from scryntic.dataset.selection import (
    SelectedCandle,
    SelectionError,
    SourceInput,
    select_candles,
    strict_eligible,
)
from scryntic.dataset.storage import DatasetStorage, DatasetStorageError
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import SchemaRef
from scryntic.domain.market import CANDLE_SCHEMA, INSTRUMENT_SCHEMA
from scryntic.domain.validation import digest, identifier
from scryntic.normalization.candle import FAKE_CANDLE_SCHEMA
from scryntic.publication.manifest import prepare_manifest
from scryntic.publication.reader import (
    PublicationReader,
    PublicationReaderError,
    ValidatedManifest,
)

_CANDLE_PRICE_DEFINITION = "decimal128(38,18) candle price"

MAX_PARQUET_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024

TABLE_SCHEMA = pa.schema(
    [
        ("start_ns", pa.int64()),
        ("interval_ns", pa.int64()),
        ("venue", pa.string()),
        ("category", pa.string()),
        ("symbol", pa.string()),
        ("open", pa.decimal128(38, 18)),
        ("high", pa.decimal128(38, 18)),
        ("low", pa.decimal128(38, 18)),
        ("close", pa.decimal128(38, 18)),
        ("volume", pa.decimal128(38, 18)),
        ("volume_unit", pa.string()),
        ("finalized", pa.bool_()),
        ("quality_flags", pa.list_(pa.string())),
        ("source_time_value", pa.int64()),
        ("source_time_unit", pa.string()),
        ("publication_time_value", pa.int64()),
        ("publication_time_unit", pa.string()),
        ("semantic_revision", pa.string()),
        ("producer", pa.string()),
        ("epoch", pa.string()),
        ("offset", pa.int64()),
        ("manifest_sha256", pa.string()),
        ("raw_object_sha256", pa.string()),
        ("normalized_object_sha256", pa.string()),
        ("input_ordinal", pa.int64()),
        ("raw_sha256", pa.string()),
        ("receipt_wall_time_ns", pa.int64()),
        ("normalized_at_ns", pa.int64()),
    ]
)


class DatasetBuildError(RuntimeError):
    """A complete validated dataset snapshot could not be produced."""


def _schema(value: SchemaRef) -> dict[str, object]:
    return {
        "name": value.name,
        "major": value.version.major,
        "minor": value.version.minor,
    }


def _lineage(value: SourceInput) -> dict[str, object]:
    identity = value.value.raw.identity
    return {
        "manifest_sha256": value.manifest_sha256,
        "raw_object_sha256": value.raw_object_sha256,
        "normalized_object_sha256": value.normalized_object_sha256,
        "input_ordinal": value.ordinal,
        "identity": {
            "producer": identity.producer,
            "epoch": identity.epoch,
            "offset": identity.offset,
        },
        "input_fingerprint": input_fingerprint(value.value),
        "input": value.value.projection(),
    }


def _row(value: SelectedCandle) -> dict[str, Any]:
    source = value.selected
    publication = source.value
    semantics = publication.semantics
    if semantics is None:
        raise DatasetBuildError("Selected candle has no semantics")
    key = semantics.key
    identity = publication.raw.identity
    return {
        "start_ns": key.start_ns,
        "interval_ns": key.interval_ns,
        "venue": key.instrument.venue,
        "category": key.instrument.category,
        "symbol": key.instrument.symbol,
        "open": Decimal(semantics.open),
        "high": Decimal(semantics.high),
        "low": Decimal(semantics.low),
        "close": Decimal(semantics.close),
        "volume": Decimal(semantics.volume),
        "volume_unit": semantics.volume_unit,
        "finalized": semantics.finalized,
        "quality_flags": list(semantics.quality_flags),
        "source_time_value": None
        if semantics.source_time is None
        else semantics.source_time.value,
        "source_time_unit": None
        if semantics.source_time is None
        else semantics.source_time.unit.value,
        "publication_time_value": None
        if semantics.publication_time is None
        else semantics.publication_time.value,
        "publication_time_unit": None
        if semantics.publication_time is None
        else semantics.publication_time.unit.value,
        "semantic_revision": semantics.revision(),
        "producer": identity.producer,
        "epoch": identity.epoch,
        "offset": identity.offset,
        "manifest_sha256": source.manifest_sha256,
        "raw_object_sha256": source.raw_object_sha256,
        "normalized_object_sha256": source.normalized_object_sha256,
        "input_ordinal": source.ordinal,
        "raw_sha256": publication.raw.envelope.content_sha256,
        "receipt_wall_time_ns": publication.outcome.receipt.wall_time_ns,
        "normalized_at_ns": publication.outcome.normalized_at_ns,
    }


def _publication_sources(validated: ValidatedManifest) -> list[SourceInput]:
    raw_object, normalized_object = validated.document.body.objects
    sources: list[SourceInput] = []
    for ordinal, (value, raw) in enumerate(
        zip(validated.inputs, validated.raw_records, strict=True)
    ):
        if value.raw != raw:
            raise DatasetBuildError("Publication correspondence disagrees")
        if (
            value.outcome.input_schema not in (FAKE_CANDLE_SCHEMA, BYBIT_CANDLE_SCHEMA)
            or value.outcome.instrument_schema != INSTRUMENT_SCHEMA
            or value.outcome.output_schema != CANDLE_SCHEMA
        ):
            raise DatasetBuildError("Unsupported normalized candle schema")
        sources.append(
            SourceInput(
                validated.document.manifest_hash,
                raw_object.sha256,
                normalized_object.sha256,
                ordinal,
                value,
            )
        )
    return sources


class DatasetBuilder:
    def __init__(
        self,
        publications: PublicationReader,
        installation: Installation,
        *,
        code_revision: str,
        dependency_lock_sha256: str,
        clock_limits: ClockLimits | None = None,
    ) -> None:
        if not isinstance(publications, PublicationReader):
            raise TypeError("Expected validated publication reader")
        identifier(code_revision)
        digest(dependency_lock_sha256)
        self._publications = publications
        self._installation = installation
        self._code_revision = code_revision
        self._dependency_lock_sha256 = dependency_lock_sha256
        self._clock_limits = ClockLimits() if clock_limits is None else clock_limits
        if not isinstance(self._clock_limits, ClockLimits):
            raise TypeError("Expected validated clock budgets")

    def _collect_inputs(
        self, request: BuildDatasetRequest
    ) -> tuple[list[SourceInput], list[dict[str, object]]]:
        manifests = [
            self._publications.resolve_exact(value) for value in request.input_manifests
        ]
        if len({item.document.manifest_hash for item in manifests}) != len(manifests):
            raise DatasetBuildError("Repeated publication manifest")
        manifests.sort(
            key=lambda item: (
                item.document.body.producer,
                item.document.body.first_ingestion.offset,
                item.document.body.epoch,
            )
        )
        sources: list[SourceInput] = []
        inputs: list[dict[str, object]] = []
        for validated in manifests:
            body = validated.document.body
            if (
                len(validated.inputs) != body.record_count
                or len(validated.raw_records) != body.record_count
            ):
                raise DatasetBuildError("Publication input coverage disagrees")
            raw_object, normalized_object = body.objects
            if (
                raw_object.role.value != "raw"
                or normalized_object.role.value != "normalized"
                or raw_object.format != RAW_PARQUET_SCHEMA
                or normalized_object.format != NORMALIZED_PARQUET_SCHEMA
            ):
                raise DatasetBuildError("Publication object roles disagree")
            inputs.append(
                {
                    "manifest_sha256": validated.document.manifest_hash,
                    "manifest": json.loads(prepare_manifest(body)),
                    "objects": [
                        {
                            "role": item.role.value,
                            "sha256": item.sha256,
                            "format": _schema(item.format),
                            "codec": item.codec,
                            "encoded_bytes": item.encoded_bytes,
                            "decoded_bytes": item.decoded_bytes,
                            "record_count": item.record_count,
                        }
                        for item in body.objects
                    ],
                }
            )
            sources.extend(_publication_sources(validated))
        return sources, inputs

    def _manifest_payload(
        self,
        request: BuildDatasetRequest,
        sources: list[SourceInput],
        inputs: list[dict[str, object]],
        selected: tuple[SelectedCandle, ...],
        rows: list[dict[str, Any]],
        excluded: tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        cutoff = request.as_observed_cutoff
        payload: dict[str, object] = {
            "schema": _schema(MANIFEST_SCHEMA),
            "dataset_schema": _schema(DATASET_SCHEMA),
            "recipe": _schema(request.recipe),
            "code_revision": self._code_revision,
            "dependency_lock_sha256": self._dependency_lock_sha256,
            "selection": "latest accepted open revision or finalization per key; duplicates add evidence; conflicts fail",
            "timing_policy": "historical-reconstruction"
            if cutoff is None
            else "strict-as-observed",
            "as_observed_cutoff": None
            if cutoff is None
            else {
                "wall_time_ns": cutoff.wall_time_ns,
                "monotonic_ns": cutoff.monotonic_ns,
                "session_id": cutoff.session_id,
                "quality": asdict(cutoff.quality),
            },
            "clock_limits": None if cutoff is None else asdict(self._clock_limits),
            "ordering": ["start_ns", "venue", "category", "symbol", "interval_ns"],
            "inputs": inputs,
            "rows": [
                {
                    "index": index,
                    "key": {
                        key: row[key]
                        for key in (
                            "start_ns",
                            "interval_ns",
                            "venue",
                            "category",
                            "symbol",
                        )
                    },
                    "semantic_revision": row["semantic_revision"],
                    "selected": _lineage(value.selected),
                    "evidence": [_lineage(source) for source in value.evidence],
                }
                for index, (row, value) in enumerate(zip(rows, selected, strict=True))
            ],
            "row_count": len(rows),
            "time_range_ns": [rows[0]["start_ns"], rows[-1]["start_ns"]],
            "coverage": {
                "manifest_count": len(inputs),
                "validated_input_count": len(sources),
                "selected_logical_row_count": len(rows),
                "first_offset": min(
                    source.value.raw.identity.offset for source in sources
                ),
                "last_offset": max(
                    source.value.raw.identity.offset for source in sources
                ),
            },
            "time_quality": sorted(
                {source.value.raw.envelope.receipt.quality.status for source in sources}
            ),
            "exclusions": list(excluded),
            "transformations": {
                "normalizer_versions": sorted(
                    {source.value.outcome.normalizer_version for source in sources}
                ),
                "selection_recipe": _schema(request.recipe),
            },
            "features": ["open", "high", "low", "close", "volume"],
            "feature_definitions": {
                "open": _CANDLE_PRICE_DEFINITION,
                "high": _CANDLE_PRICE_DEFINITION,
                "low": _CANDLE_PRICE_DEFINITION,
                "close": _CANDLE_PRICE_DEFINITION,
                "volume": "decimal128(38,18) candle volume; unit in volume_unit",
            },
            "labels": [],
            "splits": [],
        }
        return payload

    def build(self, request: BuildDatasetRequest) -> BuildDatasetResult:
        if not isinstance(request, BuildDatasetRequest):
            raise TypeError("Expected build request")
        cutoff = request.as_observed_cutoff
        if request.recipe == CANDLE_RECIPE_SCHEMA:
            if cutoff is not None:
                raise DatasetBuildError("Historical recipe cannot claim strict timing")
        elif request.recipe == AS_OBSERVED_CANDLE_RECIPE_SCHEMA:
            if cutoff is None or time_interval(cutoff, self._clock_limits) is None:
                raise DatasetBuildError("Strict selection requires a bounded cutoff")
        else:
            raise DatasetBuildError("Unsupported dataset recipe")
        try:
            sources, inputs = self._collect_inputs(request)
            eligible, excluded = (
                strict_eligible(tuple(sources), cutoff, self._clock_limits)
                if cutoff is not None
                else (tuple(sources), ())
            )
            selected = select_candles(eligible)
            rows = [_row(value) for value in selected]
            table = pa.Table.from_pylist(rows, schema=TABLE_SCHEMA)
            storage = DatasetStorage(self._installation)
            parquet_sha256, parquet_bytes = storage.write_table(
                self._installation, table, max_bytes=MAX_PARQUET_BYTES
            )
            payload = self._manifest_payload(
                request, sources, inputs, selected, rows, excluded
            )
            payload["parquet"] = {
                "sha256": parquet_sha256,
                "encoded_bytes": parquet_bytes,
                "schema": _schema(DATASET_SCHEMA),
                "row_count": len(rows),
            }
            manifest_sha256, _ = storage.write_manifest(
                self._installation,
                canonical_json_bytes(cast(JsonValue, payload)),
                max_bytes=MAX_MANIFEST_BYTES,
            )
            return BuildDatasetResult(
                DatasetRef(manifest_sha256, DATASET_SCHEMA, len(rows))
            )
        except DatasetBuildError:
            raise
        except (
            PublicationReaderError,
            SelectionError,
            DatasetStorageError,
            OSError,
            ValueError,
            TypeError,
            pa.ArrowException,
        ) as exc:
            raise DatasetBuildError(
                "Unable to build complete dataset snapshot"
            ) from exc


class DatasetReader:
    def __init__(self, installation: Installation) -> None:
        self._storage = DatasetStorage(installation)

    def read_manifest(self, reference: DatasetRef) -> dict[str, Any]:
        if reference.schema != DATASET_SCHEMA:
            raise DatasetBuildError("Unsupported dataset schema")
        try:
            data = self._storage.read_artifact(
                reference.manifest_sha256, "json", max_bytes=MAX_MANIFEST_BYTES
            )
            payload = json.loads(data)
            if not isinstance(payload, dict) or canonical_json_bytes(payload) != data:
                raise DatasetBuildError("Invalid canonical dataset manifest")
            if (
                payload.get("schema") != _schema(MANIFEST_SCHEMA)
                or payload.get("dataset_schema") != _schema(reference.schema)
                or payload.get("row_count") != reference.row_count
            ):
                raise DatasetBuildError("Dataset reference and manifest disagree")
            rows = payload.get("rows")
            if not isinstance(rows, list) or len(rows) != reference.row_count:
                raise DatasetBuildError("Dataset lineage and row count disagree")
            return payload
        except DatasetBuildError:
            raise
        except (DatasetStorageError, OSError, ValueError, TypeError) as exc:
            raise DatasetBuildError("Unable to read dataset manifest") from exc

    def read_table(self, reference: DatasetRef) -> pa.Table:
        manifest = self.read_manifest(reference)
        if "imports" in manifest:
            raise DatasetBuildError("Imported datasets require restricted inspection")
        descriptor = manifest.get("parquet")
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("schema") != _schema(reference.schema)
            or descriptor.get("row_count") != reference.row_count
        ):
            raise DatasetBuildError("Dataset Parquet descriptor disagrees")
        try:
            value = descriptor["sha256"]
            if not isinstance(value, str):
                raise DatasetBuildError("Invalid Parquet digest")
            data = self._storage.read_artifact(
                value, "parquet", max_bytes=MAX_PARQUET_BYTES
            )
            if len(data) != descriptor["encoded_bytes"]:
                raise DatasetBuildError("Dataset Parquet size disagrees")
            table = pq.read_table(pa.BufferReader(data))
            if table.schema != TABLE_SCHEMA or table.num_rows != reference.row_count:
                raise DatasetBuildError("Dataset Parquet schema or row count disagrees")
            return table
        except DatasetBuildError:
            raise
        except (
            DatasetStorageError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            pa.ArrowException,
        ) as exc:
            raise DatasetBuildError("Unable to read dataset Parquet") from exc
