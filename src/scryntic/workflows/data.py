"""Local accepted-data operations through the existing restricted F18/F19 APIs."""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol, cast

from scryntic.application.data_operations import (
    DataInspectRequest,
    DatasetBuildRequest,
    DatasetExportRequest,
    DatasetInspectRequest,
    ReplayRequest,
)
from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
)
from scryntic.configuration.paths import Installation
from scryntic.dataset.protocol import clock_from
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy
from scryntic.dataset.schemas import F18_RECIPE_SCHEMA, schema_projection
from scryntic.dataset.service import DatasetService
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.identity import InstrumentId
from scryntic.domain.time import ClockSample
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportError
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import PreparedReplay, ReplayService
from scryntic.workflows.wire import result


class DataContext(Protocol):
    @property
    def installation(self) -> Installation: ...

    @property
    def code_revision(self) -> str: ...

    @property
    def dependency_lock_sha256(self) -> str: ...

    async def run_io[T](self, function: Callable[[], T]) -> T: ...

    def read_config_json(self, leaf: str, maxbytes: int) -> object: ...

    def export_bundle(
        self, leaf: str, files: dict[str, bytes]
    ) -> dict[str, object]: ...


def dataset_service(catalog: ImportCatalog, context: DataContext) -> DatasetService:
    return DatasetService(
        catalog,
        context.installation,
        code_revision=context.code_revision,
        dependency_lock_sha256=context.dependency_lock_sha256,
    )


def prepare_replay(
    context: DataContext, manifest_sha256: str, config: ReplayConfig
) -> PreparedReplay:
    """Materialize exact pinned replay on the caller's retained worker owner.

    Every reopen rechecks identity, active pins and native artifacts. The returned
    reader owns only bounded primitives, so the catalog closes before returning.
    Caller-supplied temporal configuration is never inferred from a physical clock.
    """
    with ImportCatalog(context.installation) as catalog:
        datasets = dataset_service(catalog, context)
        reference = datasets.resolve(manifest_sha256)
        return ReplayService(
            datasets,
            code_revision=context.code_revision,
            dependency_lock_sha256=context.dependency_lock_sha256,
        ).prepare(reference, config)


def _reference(reference: DatasetRef) -> dict[str, object]:
    return {
        "manifest_sha256": reference.manifest_sha256,
        "schema": schema_projection(reference.schema),
        "row_count": reference.row_count,
    }


def _clock(value: object) -> ClockSample:
    if type(value) is not dict or set(value) != {
        "wall_time_ns",
        "monotonic_ns",
        "session_id",
        "quality",
    }:
        raise ValueError("Expected a complete captured clock sample")
    document = cast(dict[str, Any], value)
    quality = document["quality"]
    if type(quality) is not dict or set(quality) != {
        "epoch",
        "status",
        "offset_ns",
        "uncertainty_ns",
        "evidence_age_ns",
    }:
        raise ValueError("Expected complete captured time quality")
    captured = clock_from(document)
    assert captured is not None
    return captured


def _coverage(value: object) -> tuple[CoverageClaim, ...]:
    if type(value) is not dict:
        raise ValueError("Expected a versioned captured coverage object")
    document = cast(dict[str, object], value)
    if (
        set(document) != {"version", "intervals"}
        or type(document["version"]) is not int
        or document["version"] != 1
    ):
        raise ValueError("Expected a versioned captured coverage object")
    intervals = document["intervals"]
    if type(intervals) is not list or len(intervals) > 4096:
        raise ValueError("Expected a bounded captured coverage list")
    claims: list[CoverageClaim] = []
    for entry in intervals:
        if type(entry) is not dict or set(entry) != {
            "instrument",
            "interval_ns",
            "start_ns",
            "end_ns",
            "status",
            "detected",
        }:
            raise ValueError("Expected a complete coverage claim")
        instrument = entry["instrument"]
        if type(instrument) is not dict or set(instrument) != {
            "venue",
            "category",
            "symbol",
        }:
            raise ValueError("Expected an explicit instrument identity")
        claims.append(
            CoverageClaim(
                InstrumentId(**instrument),
                entry["interval_ns"],
                entry["start_ns"],
                entry["end_ns"],
                entry["status"],
                _clock(entry["detected"]),
            )
        )
    return tuple(claims)


def _replay_config(request: ReplayRequest) -> ReplayConfig:
    return ReplayConfig(
        request.start_ns,
        request.validation_start_ns,
        request.test_start_ns,
        request.end_ns,
        mode=request.mode,
        decision_delay_ns=request.decision_delay_ns,
        decision_uncertainty_ns=request.decision_uncertainty_ns,
        coverage=request.coverage,
        time_quality=request.time_quality,
    )


class DataWorkflows:
    def __init__(self, context: DataContext) -> None:
        self._context = context

    async def execute(self, request: Request) -> ApplicationResult:
        # SQLite catalogs and all snapshots are created, used and closed by one
        # worker invocation. Cancelling a presentation cannot transfer ownership.
        return await self._context.run_io(lambda: self._execute(request))

    def _execute(self, request: Request) -> ApplicationResult:
        try:
            with ImportCatalog(self._context.installation) as catalog:
                datasets = dataset_service(catalog, self._context)
                if isinstance(request, DataInspectRequest):
                    document = catalog.resolve(request.manifest_sha256)
                    body = document.body
                    return result(
                        request.operation,
                        {
                            "manifest_sha256": document.manifest_hash,
                            "producer": body.producer,
                            "epoch": body.epoch,
                            "sequence": body.sequence,
                            "record_count": body.record_count,
                            "validated": True,
                            "objects": [
                                {
                                    "role": item.role.value,
                                    "sha256": item.sha256,
                                    "schema": schema_projection(item.format),
                                    "codec": item.codec,
                                    "encoded_bytes": item.encoded_bytes,
                                    "decoded_bytes": item.decoded_bytes,
                                    "record_count": item.record_count,
                                }
                                for item in body.objects
                            ],
                        },
                    )
                if isinstance(request, DatasetBuildRequest):
                    return self._build(datasets, request)
                if isinstance(request, DatasetInspectRequest):
                    reference = datasets.resolve(request.manifest_sha256)
                    rows = datasets.inspect(
                        reference, offset=request.offset, limit=request.limit
                    )
                    end = min(reference.row_count, request.offset + len(rows))
                    return result(
                        request.operation,
                        {
                            "dataset": _reference(reference),
                            "offset": request.offset,
                            "limit": request.limit,
                            "rows": rows,
                            "next_offset": end if end < reference.row_count else None,
                        },
                    )
                if isinstance(request, DatasetExportRequest):
                    reference = datasets.resolve(request.manifest_sha256)
                    manifest, parquet = datasets.export(reference)
                    exported = self._context.export_bundle(
                        request.destination,
                        {
                            "dataset.manifest.json": manifest,
                            "dataset.parquet": parquet,
                        },
                    )
                    return result(
                        request.operation,
                        {"dataset": _reference(reference), "export": exported},
                    )
                if isinstance(request, ReplayRequest):
                    return self._evaluate(datasets, request)
            raise ApplicationError(ErrorCode.UNSUPPORTED)
        except ApplicationError:
            raise
        except (ImportError, FileNotFoundError):
            raise ApplicationError(ErrorCode.UNAVAILABLE) from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ApplicationError(ErrorCode.INVALID) from None
        except OSError:
            raise ApplicationError(ErrorCode.FAILED) from None

    def _build(
        self, datasets: DatasetService, request: DatasetBuildRequest
    ) -> ApplicationResult:
        cutoff = None
        coverage: tuple[CoverageClaim, ...] = ()
        try:
            if request.cutoff_leaf:
                cutoff = _clock(
                    self._context.read_config_json(request.cutoff_leaf, 4096)
                )
            if request.coverage_leaf:
                coverage = _coverage(
                    self._context.read_config_json(request.coverage_leaf, 1024 * 1024)
                )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ApplicationError(ErrorCode.CONFIGURATION) from None
        policy = RecipePolicy(
            request.mode,
            request.revision,
            request.coverage,
            request.time_quality,
            request.finalized_only,
            request.lag_steps,
            request.label_horizon_steps,
        )
        reference = datasets.build(
            BuildDatasetRequest(request.manifests, F18_RECIPE_SCHEMA, cutoff),
            policy=policy,
            coverage=coverage,
        ).dataset
        return result(
            request.operation,
            {
                "dataset": _reference(reference),
                "input_manifests": sorted(request.manifests),
                "recipe": schema_projection(F18_RECIPE_SCHEMA),
                "policy": policy.projection(),
                "pins": datasets.pins(reference),
            },
        )

    def _evaluate(
        self, datasets: DatasetService, request: ReplayRequest
    ) -> ApplicationResult:
        reference = datasets.resolve(request.manifest_sha256)
        replay = ReplayService(
            datasets,
            code_revision=self._context.code_revision,
            dependency_lock_sha256=self._context.dependency_lock_sha256,
        )
        config = _replay_config(request)
        report = replay.evaluate(reference, config)
        document = json.loads(report.canonical_bytes)
        fields: dict[str, object] = {
            "dataset": _reference(reference),
            "configuration": asdict(config),
            "report_sha256": report.sha256,
            "report_bytes": len(report.canonical_bytes),
            "status": document["status"],
            "counts": document["counts"],
            "metrics": document["metrics"][:128],
            "metrics_count": len(document["metrics"]),
            "metrics_truncated": len(document["metrics"]) > 128,
        }
        if request.destination:
            fields["export"] = self._context.export_bundle(
                request.destination, {"replay.json": report.canonical_bytes}
            )
        return result(request.operation, fields)
