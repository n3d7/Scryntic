"""Dataset application boundary: sealed bytes in, bounded primitives/bytes out.

No native parser, Arrow object, arbitrary query or path is admitted here.
"""

import base64
import os
from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
from typing import Any, cast

from scryntic.application.dto import BuildDatasetRequest, BuildDatasetResult
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.clock.policy import time_interval
from scryntic.configuration.clock import ClockLimits
from scryntic.configuration.paths import Installation
from scryntic.dataset.pins import DatasetPins
from scryntic.dataset.protocol import (
    MAX_DATASET_BYTES,
    MAX_INSPECT_ROWS,
    MAX_PROVENANCE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    environment,
    primitive_document,
    validate_rows,
)
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy
from scryntic.dataset.schemas import (
    AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
    CANDLE_RECIPE_SCHEMA,
    DATASET_SCHEMA,
    EVOLVED_DATASET_SCHEMA,
    EVOLVED_MANIFEST_SCHEMA,
    schema_from,
    schema_projection,
)
from scryntic.dataset.schemas import (
    F18_RECIPE_SCHEMA as F18_RECIPE_SCHEMA,
)
from scryntic.dataset.storage import DatasetStorage
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.validation import digest, identifier, integer
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.filesystem import sealed_bytes
from scryntic.imports.launcher import LinuxDecoder
from scryntic.imports.protocol import ImportError
from scryntic.publication.manifest import prepare_manifest


class DatasetService:
    def __init__(
        self,
        catalog: ImportCatalog,
        installation: Installation,
        *,
        code_revision: str,
        dependency_lock_sha256: str,
        clock_limits: ClockLimits | None = None,
    ) -> None:
        identifier(code_revision)
        digest(dependency_lock_sha256)
        self._catalog = catalog
        self._installation = installation
        self._storage = DatasetStorage(installation)
        self._pins = DatasetPins(installation)
        self._decoder = LinuxDecoder(catalog.limits)
        self._limits = ClockLimits() if clock_limits is None else clock_limits
        self._environment = environment(
            code_revision, dependency_lock_sha256, self._limits
        )

    def _launch(
        self, request: dict[str, Any], files: tuple[int, ...], names: tuple[str, ...]
    ) -> dict[str, Any]:
        data = canonical_json_bytes(request)
        if len(data) > MAX_REQUEST_BYTES:
            raise ImportError("Analytical request limit")
        fd = sealed_bytes(data)
        try:
            response = primitive_document(
                self._decoder.analytical(
                    (fd, *files), ("request", *names), MAX_RESULT_BYTES
                ),
                MAX_RESULT_BYTES,
            )
        finally:
            os.close(fd)
        if response.get("request_sha256") != sha256(data).hexdigest():
            raise ImportError("Analytical response identity mismatch")
        return response

    def build(
        self,
        request: BuildDatasetRequest,
        *,
        policy: RecipePolicy | None = None,
        coverage: tuple[CoverageClaim, ...] = (),
    ) -> BuildDatasetResult:
        F18_RECIPE_SCHEMA.require_readable(request.recipe)
        strict = request.recipe == AS_OBSERVED_CANDLE_RECIPE_SCHEMA
        default = RecipePolicy(mode="as-observed") if strict else RecipePolicy()
        chosen = default if policy is None else policy
        if request.recipe in (
            CANDLE_RECIPE_SCHEMA,
            AS_OBSERVED_CANDLE_RECIPE_SCHEMA,
        ) and (chosen != default or coverage):
            raise ValueError("Extended policy requires recipe 1.2")
        cutoff = request.as_observed_cutoff
        if chosen.mode == "as-observed":
            if cutoff is None or time_interval(cutoff, self._limits) is None:
                raise ValueError("Strict selection requires a bounded cutoff")
        elif cutoff is not None:
            raise ValueError("Historical reconstruction cannot claim strict timing")
        if (
            type(coverage) is not tuple
            or len(coverage) > 4096
            or any(not isinstance(item, CoverageClaim) for item in coverage)
        ):
            raise ValueError("Invalid coverage snapshot")
        coverage_data = [item.projection() for item in coverage]
        with self._catalog.analytical_inputs(
            tuple(sorted(request.input_manifests))
        ) as inputs:
            import_metadata = [
                {
                    "manifest_sha256": item[0].manifest_hash,
                    "receipt": primitive_document(item[1], 4096),
                }
                for item in inputs
            ]
            message = {
                "protocol": 1,
                "operation": "build",
                "recipe": schema_projection(request.recipe),
                "policy": chosen.projection(),
                "cutoff": None if cutoff is None else asdict(cutoff),
                "coverage_claims": coverage_data,
                "environment": self._environment,
                "imports": import_metadata,
                "input_count": len(inputs),
                "accepted_inputs": [
                    {
                        "manifest_sha256": item[0].manifest_hash,
                        "body": primitive_document(
                            prepare_manifest(item[0].body),
                            self._catalog.limits.max_manifest_bytes,
                        ),
                        "objects": [
                            {
                                "role": obj.role.value,
                                "sha256": obj.sha256,
                                "schema": schema_projection(obj.format),
                                "codec": obj.codec,
                                "encoded_bytes": obj.encoded_bytes,
                                "decoded_bytes": obj.decoded_bytes,
                                "record_count": obj.record_count,
                            }
                            for obj in item[0].body.objects
                        ],
                    }
                    for item in inputs
                ],
            }
            response = self._launch(
                message,
                tuple(fd for item in inputs for fd in item[2]),
                tuple(
                    name
                    for i in range(len(inputs))
                    for name in (f"m{i}", f"r{i}", f"n{i}")
                ),
            )
        return self._install(response, message)

    def _install(
        self, response: dict[str, Any], request: dict[str, Any]
    ) -> BuildDatasetResult:
        try:
            if set(response) != {"request_sha256", "manifest", "parquet_base64"}:
                raise ValueError("Unexpected result")
            manifest = response["manifest"]
            parquet = base64.b64decode(response["parquet_base64"], validate=True)
            data = canonical_json_bytes(manifest)
            if (
                not 0 < len(parquet) <= MAX_DATASET_BYTES
                or len(data) > MAX_PROVENANCE_BYTES
            ):
                raise ValueError("Artifact limit")
            expected_schema = (
                DATASET_SCHEMA
                if schema_from(request["recipe"]) != F18_RECIPE_SCHEMA
                else EVOLVED_DATASET_SCHEMA
            )
            if (
                manifest["schema"] != schema_projection(EVOLVED_MANIFEST_SCHEMA)
                or manifest["dataset_schema"] != schema_projection(expected_schema)
                or manifest["recipe"] != request["recipe"]
                or manifest["policy"] != request["policy"]
                or manifest["as_observed_cutoff"] != request["cutoff"]
                or manifest["imports"] != request["imports"]
                or manifest["coverage_claims"] != request["coverage_claims"]
                or manifest["environment"] != request["environment"]
                or manifest["inputs"] != request["accepted_inputs"]
                or manifest["coverage_sha256"]
                != sha256(canonical_json_bytes(request["coverage_claims"])).hexdigest()
                or sorted(item["manifest_sha256"] for item in manifest["inputs"])
                != sorted(item["manifest_sha256"] for item in request["imports"])
                or manifest["parquet"]["sha256"] != sha256(parquet).hexdigest()
                or manifest["parquet"]["encoded_bytes"] != len(parquet)
            ):
                raise ValueError("Result provenance mismatch")
            reference = DatasetRef(
                sha256(data).hexdigest(), expected_schema, manifest["row_count"]
            )
            self._validate_manifest(reference, manifest)
            self._storage.write_bytes(
                self._installation, parquet, "parquet", max_bytes=MAX_DATASET_BYTES
            )
            self._storage.write_manifest(
                self._installation, data, max_bytes=MAX_PROVENANCE_BYTES
            )
            self._pins.create(reference, manifest)
            return BuildDatasetResult(reference)
        except Exception:
            raise ImportError("Dataset build result rejected") from None

    @staticmethod
    def _validate_manifest(reference: DatasetRef, manifest: dict[str, Any]) -> None:
        EVOLVED_DATASET_SCHEMA.require_readable(reference.schema)
        EVOLVED_MANIFEST_SCHEMA.require_readable(schema_from(manifest["schema"]))
        F18_RECIPE_SCHEMA.require_readable(schema_from(manifest["recipe"]))
        if (
            manifest["dataset_schema"] != schema_projection(reference.schema)
            or type(manifest["row_count"]) is not int
            or manifest["row_count"] != reference.row_count
            or len(manifest["rows"]) != reference.row_count
            or manifest["parquet"]["schema"] != schema_projection(reference.schema)
            or type(manifest["parquet"]["row_count"]) is not int
            or manifest["parquet"]["row_count"] != reference.row_count
        ):
            raise ValueError("Dataset manifest identity mismatch")
        digest(manifest["parquet"]["sha256"])
        integer(manifest["parquet"]["encoded_bytes"], 1)

    def read_manifest(self, reference: DatasetRef) -> dict[str, Any]:
        EVOLVED_DATASET_SCHEMA.require_readable(reference.schema)
        try:
            manifest = primitive_document(
                self._storage.read_artifact(
                    reference.manifest_sha256, "json", max_bytes=MAX_PROVENANCE_BYTES
                ),
                MAX_PROVENANCE_BYTES,
            )
            self._validate_manifest(reference, manifest)
            return manifest
        except Exception:
            raise ImportError("Dataset manifest rejected") from None

    def _read(
        self, reference: DatasetRef, offset: int, limit: int
    ) -> tuple[dict[str, Any], bytes]:
        integer(offset, 0)
        integer(limit, 1)
        if limit > MAX_INSPECT_ROWS:
            raise ValueError("Inspection row limit")
        manifest = self.read_manifest(reference)
        data = canonical_json_bytes(manifest)
        parquet = self._storage.read_artifact(
            manifest["parquet"]["sha256"], "parquet", max_bytes=MAX_DATASET_BYTES
        )
        if len(parquet) != manifest["parquet"]["encoded_bytes"]:
            raise ImportError("Dataset length mismatch")
        with ExitStack() as resources:
            files = (sealed_bytes(data), sealed_bytes(parquet))
            for fd in files:
                resources.callback(os.close, fd)
            response = self._launch(
                {
                    "protocol": 1,
                    "operation": "inspect",
                    "manifest_sha256": reference.manifest_sha256,
                    "dataset_schema": schema_projection(reference.schema),
                    "row_count": reference.row_count,
                    "offset": offset,
                    "limit": limit,
                },
                files,
                ("manifest", "parquet"),
            )
        if (
            set(response) != {"request_sha256", "rows"}
            or type(response["rows"]) is not list
            or len(response["rows"]) != min(limit, max(0, reference.row_count - offset))
        ):
            raise ImportError("Invalid inspection response")
        validate_rows(
            response["rows"], evolved=reference.schema == EVOLVED_DATASET_SCHEMA
        )
        return response, parquet

    def inspect(
        self, reference: DatasetRef, *, offset: int = 0, limit: int = MAX_INSPECT_ROWS
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]], self._read(reference, offset, limit)[0]["rows"]
        )

    def export(self, reference: DatasetRef) -> tuple[bytes, bytes]:
        _, parquet = self._read(reference, 0, 1)
        return canonical_json_bytes(self.read_manifest(reference)), parquet

    def pins(self, reference: DatasetRef) -> dict[str, Any]:
        return self._pins.read(reference, self.read_manifest(reference))

    def retire(self, reference: DatasetRef) -> None:
        self._pins.retire(reference, self.read_manifest(reference))
