"""Bounded, model-agnostic F19 comparison through admitted F20 jobs."""

import time
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from scryntic.application.providers import ForecastRequest
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.dataset.protocol import MAX_RESULT_BYTES
from scryntic.domain.dataset import DatasetRef
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobStore
from scryntic.models.definitions import definition, policy
from scryntic.models.preprocessing import ReplayJobInputs, ReplayWindows
from scryntic.models.provider import select_provider
from scryntic.models.selection import ModelSelection
from scryntic.models.window import ForecastWindow
from scryntic.replay.contracts import ReplayClock
from scryntic.replay.evaluation import (
    ForecastCase,
    collect_cases,
    evaluate,
    score_cases,
    series_projection,
)
from scryntic.replay.reader import CandleReplayReader
from scryntic.replay.service import EvaluationReport, PreparedReplay


def comparison_plan(
    reader: CandleReplayReader, reference: DatasetRef, *, per_partition: int = 2
) -> tuple[list[ForecastCase], dict[str, ForecastWindow], list[dict[str, Any]]]:
    if type(per_partition) is not int or not 1 <= per_partition <= 8:
        raise ValueError("Comparison sample exceeds bounded qualification quota")
    if not 1 <= reader.policy.label_horizon_steps <= 24:
        raise ValueError("Comparison requires a supported forecast horizon")
    baseline = evaluate(reader)
    eligible = {row["row_index"] for row in baseline["predictions"]}
    candidates, exclusions = collect_cases(reader)
    windows = ReplayWindows(reader, reference)
    counts: dict[tuple[Any, ...], int] = {}
    cases: list[ForecastCase] = []
    inputs: dict[str, ForecastWindow] = {}
    for case in candidates:
        if case.features.row_index not in eligible:
            continue
        key = (*case.features.series, case.partition)
        if counts.get(key, 0) >= per_partition:
            continue
        try:
            context = windows.at(
                case.features.row_index,
                ReplayClock(
                    case.features.decision_ns,
                    uncertainty_ns=reader.config.decision_uncertainty_ns,
                ).sample,
            )
        except ValueError:
            exclusions.append(
                {
                    "row_index": case.features.row_index,
                    "partition": case.partition,
                    "reason": "insufficient-context",
                }
            )
            continue
        if len(cases) >= 24:
            raise ValueError("Comparison exceeds total qualification quota")
        inputs[f"f22-case-{len(cases)}"] = context
        cases.append(case)
        counts[key] = counts.get(key, 0) + 1
    return cases, inputs, exclusions


def comparison_metrics(cases: list[ForecastCase]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for series in sorted({case.features.series for case in cases}):
        for partition in ("train", "validation", "test"):
            chosen = [
                case
                for case in cases
                if case.features.series == series and case.partition == partition
            ]
            if chosen:
                results.append(
                    {
                        "series": series_projection(series),
                        "partition": partition,
                        **score_cases(chosen),
                    }
                )
    return results


async def evaluate_selected(
    prepared: PreparedReplay,
    reference: DatasetRef,
    selection: ModelSelection,
    installation: Installation,
    *,
    per_partition: int = 2,
) -> EvaluationReport:
    """Two distinct jobs per case qualify execution repeatability, not cache reuse."""
    reader = prepared.reader
    cases, windows, exclusions = comparison_plan(
        reader, reference, per_partition=per_partition
    )
    if not cases:
        raise ValueError("No comparable quality-eligible replay cases")
    review_identity = (
        definition(selection.selected).review(selection.runtime_sha256).sha256
    )
    # Each qualification invocation executes fresh jobs. Reusing durable job
    # identities would either conflict with a new deadline or reuse cached
    # evidence instead of exercising the worker again.
    qualification_id = uuid4().hex
    identities = {
        name: "f22-"
        + sha256(
            canonical_json_bytes(
                {
                    "qualification_id": qualification_id,
                    "review_sha256": review_identity,
                    "dataset_sha256": reference.manifest_sha256,
                    "replay": reader.config.projection(),
                    "horizon": reader.policy.label_horizon_steps,
                    "seed": 0,
                    "context": window.projection(),
                }
            )
        ).hexdigest()
        for name, window in windows.items()
    }
    repeated = {
        identities[name] + f"-run-{run}": window
        for name, window in windows.items()
        for run in range(2)
    }
    inputs = ReplayJobInputs(repeated)
    provider = select_provider(selection, inputs)
    artifacts = ImmutableForecastStore(installation)
    store = JobStore(installation, clock=time.time_ns)
    service = JobService(
        store, (provider,), policy((provider.review,)), artifacts, inputs, max_active=1
    )
    rows: list[dict[str, Any]] = []
    predictions: list[ForecastCase] = []
    try:
        for (name, window), case in zip(windows.items(), cases, strict=True):
            points: list[Any] | None = None
            executions: list[dict[str, Any]] = []
            for run in range(2):
                request = ForecastRequest(
                    request_id=identities[name] + f"-run-{run}",
                    dataset=reference,
                    target="close",
                    frequency_ns=window.frequency_ns,
                    horizon=reader.policy.label_horizon_steps,
                )
                service.submit(
                    request,
                    provider.review.descriptor.provider_id,
                    intended_use="forecast-research",
                    deadline_ns=time.time_ns() + 240 * 10**9,
                    seed=0,
                )
                record = await service.run(request.request_id)
                if record.state.value != "succeeded":
                    raise RuntimeError(
                        f"Qualification job {request.request_id} failed: {record.state.value}/{record.reason}"
                    )
                artifact = service.result(request.request_id)
                value = artifacts.read(artifact)["result"]["points"]
                if points is not None and points != value:
                    raise ValueError("Independent repeated forecast differs")
                points = value
                worker = provider.worker
                executions.append(
                    {
                        "artifact": asdict(artifact),
                        "effective_controls": worker.last_controls,
                        "resource_use": worker.last_usage,
                    }
                )
            assert points is not None
            prediction = Decimal(points[-1]["value"])
            predictions.append(replace(case, prediction=prediction))
            rows.append(
                {
                    "row_index": case.features.row_index,
                    "partition": case.partition,
                    "context_rows": len(window.closes),
                    "points": points,
                    "executions": executions,
                    "actual": str(case.target.value),
                    "label_end_ns": case.target.end_ns,
                    "decision_ns": case.features.decision_ns,
                }
            )
    finally:
        await service.aclose()
        store.close()
    report: dict[str, Any] = {
        "version": "f22-provider-comparison-v1",
        "qualification_id": qualification_id,
        "selected_model": selection.selected,
        "review_sha256": provider.review.sha256,
        "model": {
            "origin": provider.review.descriptor.model.origin,
            "revision": provider.review.descriptor.model.revision,
            "artifacts": list(provider.review.verified_artifacts),
        },
        "dataset": prepared.dataset,
        "environment": prepared.environment,
        "replay": reader.config.projection(),
        "sampling": {
            "per_series_partition": per_partition,
            "policy": "first-eligible-with-contiguous-context-v1",
            "repeat_jobs": 2,
        },
        "baseline": comparison_metrics(cases),
        "selected": comparison_metrics(predictions),
        "predictions": rows,
        "exclusions": exclusions,
        "limitations": [
            "Historical reconstruction is not an as-observed execution claim.",
            "Pretraining corpus overlap is unknown; chronological replay does not prove model training holdout.",
            "Determinism is qualified only for the recorded runtime and host.",
        ],
    }
    data = canonical_json_bytes(report)
    if len(data) > MAX_RESULT_BYTES:
        raise ValueError("Comparison report exceeds analytical byte limit")
    return EvaluationReport(data)
