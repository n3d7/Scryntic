"""Provider choice changes neither hindsight-free case selection nor F20 jobs."""

import asyncio
import json
from pathlib import Path

import pytest

from scryntic.application.analysis import ForecastArtifactRef
from scryntic.application.providers import ForecastPoint, ForecastResult
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.domain.dataset import DatasetRef
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.codec import encode_response
from scryntic.jobs.contracts import JobAttempt
from scryntic.model_worker.launcher import CPUWorker
from scryntic.models.evaluation import comparison_plan, evaluate_selected
from scryntic.models.inventory import RuntimeBundle
from scryntic.models.selection import ModelSelection
from scryntic.models.window import ForecastWindow
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.reader import CandleReplayReader
from scryntic.replay.service import PreparedReplay
from tests.normalization.helpers import installation
from tests.replay.helpers import START, STEP, snapshot


def test_plan_preserves_partition_purging_and_context_exclusions() -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(
        manifest,
        rows,
        ReplayConfig(
            START,
            START + 4 * STEP,
            START + 8 * STEP,
            START + 12 * STEP,
            coverage="include",
        ),
    )
    reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    cases, windows, exclusions = comparison_plan(reader, reference, per_partition=1)
    assert {case.partition for case in cases} == {"train", "validation", "test"}
    assert len(cases) == len(windows) == 3
    assert all(case.target.end_ns < case.score_cutoff_ns + 1 for case in cases)
    assert all(
        window.starts[-1] <= case.features.decision_ns - STEP
        for case, window in zip(cases, windows.values(), strict=True)
    )
    assert any(item["reason"] == "insufficient-context" for item in exclusions)


def test_excluded_quality_is_not_reenabled_for_model_evaluation() -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(
        manifest,
        rows,
        ReplayConfig(START, START + 4 * STEP, START + 8 * STEP, START + 12 * STEP),
    )
    reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    cases, windows, exclusions = comparison_plan(reader, reference, per_partition=1)
    assert not cases
    assert not windows
    assert any(item["reason"] == "coverage" for item in exclusions)


@pytest.mark.parametrize("selected", ["fake-persistence", "fake-trend", "timesfm-2.5"])
def test_selection_uses_identical_jobs_artifacts_and_comparison_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    manifest, rows = snapshot()
    reader = CandleReplayReader(
        manifest,
        rows,
        ReplayConfig(
            START,
            START + 4 * STEP,
            START + 8 * STEP,
            START + 12 * STEP,
            coverage="include",
        ),
    )
    reference = DatasetRef("a" * 64, EVOLVED_DATASET_SCHEMA, len(rows))
    bundle = RuntimeBundle(
        Path("/approved-model"), Path("/approved-runtime"), "d" * 64, ()
    )
    monkeypatch.setattr(
        RuntimeBundle, "read", classmethod(lambda cls, a, r, approved, assets: bundle)
    )
    seen: list[JobAttempt] = []

    async def primitive(
        self: CPUWorker, attempt: JobAttempt, window: ForecastWindow | None = None
    ) -> bytes:
        seen.append(attempt)
        if window is not None:
            window.validate(attempt.job)
        request = attempt.job.forecast
        result = ForecastResult(
            request.request_id,
            request.dataset,
            attempt.job.review.descriptor.provider_id,
            attempt.job.review.descriptor.model.revision or "",
            tuple(
                ForecastPoint(
                    attempt.job.inputs.verified.last_start_ns
                    + (i + 1) * request.frequency_ns,
                    attempt.job.inputs.closes[-1],
                )
                for i in range(request.horizon)
            ),
        )
        return encode_response(attempt, result)

    monkeypatch.setattr(CPUWorker, "execute", primitive)
    monkeypatch.setattr(CPUWorker, "execute_window", primitive)
    choice = (
        ModelSelection(
            selected, bundle.artifacts, bundle.runtime, bundle.inventory_sha256
        )
        if selected == "timesfm-2.5"
        else ModelSelection(selected)
    )
    paths = installation(tmp_path)
    prepared = PreparedReplay(
        reader, {"manifest_sha256": reference.manifest_sha256}, {}
    )
    report = asyncio.run(
        evaluate_selected(
            prepared,
            reference,
            choice,
            paths,
            per_partition=1,
        )
    )
    value = json.loads(report.canonical_bytes)
    assert value["selected_model"] == selected
    assert value["baseline"] == value["selected"]
    assert len(seen) == 6
    assert len({attempt.job.job_id for attempt in seen}) == 6
    assert all(len(attempt.job.inputs.closes) == 2 for attempt in seen)
    assert all(len(row["executions"]) == 2 for row in value["predictions"])
    artifacts = ImmutableForecastStore(paths)
    for row in value["predictions"]:
        for execution in row["executions"]:
            ref = execution["artifact"]
            payload = artifacts.read(
                ForecastArtifactRef(
                    ref["sha256"], reference, ref["provider_id"], ref["model_revision"]
                )
            )
            assert payload["seed"] == 0
            assert payload["hardware"] == "cpu"
            assert payload["determinism"]["seed"] == 0
            assert payload["determinism"]["hardware"] == "cpu"
            assert payload["determinism"]["runtime"] == (
                "cpython-3.12-cpu-v1" if selected == "timesfm-2.5" else "cpython-3.12"
            )
            assert "fixture only" not in payload["determinism"]["limitations"]
            assert "not guaranteed" in payload["determinism"]["limitations"]
    repeated = json.loads(
        asyncio.run(
            evaluate_selected(prepared, reference, choice, paths, per_partition=1)
        ).canonical_bytes
    )
    assert repeated["qualification_id"] != value["qualification_id"]
    assert repeated["selected"] == value["selected"]
    assert len(seen) == 12
    assert len({attempt.job.job_id for attempt in seen}) == 12
