import io
import json
import sys
from dataclasses import replace
from typing import Any

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.durable import DurableAttempt
from scryntic.model_worker.bootstrap import main
from scryntic.model_worker.launcher import CPUWorker
from scryntic.model_worker.profile import BASELINE_CPU, IsolationError
from scryntic.research.baseline_jobs import (
    EXPECTED_INPUT_SHA256,
    BaselineJob,
    decode_response,
    encode_attempt,
)


@pytest.mark.parametrize("mode", ["execute", "baseline-probe"])
def test_missing_controls_fail_before_baseline_fitting(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    attempt = DurableAttempt(
        BaselineJob("zero", EXPECTED_INPUT_SHA256, 100, 100 + 180 * 10**9), 1, "a" * 32
    )
    request = canonical_json_bytes(
        {
            "baseline_attempt": canonical_json_bytes(encode_attempt(attempt)).decode(),
            "baseline_input": "{}",
            "host": {},
            "mode": mode,
        }
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.BytesIO(request))
    monkeypatch.setattr(
        "scryntic.imports.bootstrap.restrict_syscalls", lambda **kwargs: None
    )
    reached: list[str] = []

    def controls(*args: object, **kwargs: object) -> None:
        reached.append("controls")
        raise IsolationError("missing effective controls")

    def fit(*args: object, **kwargs: object) -> None:
        reached.append("fit")
        pytest.fail("fitter reached without controls")

    monkeypatch.setattr("scryntic.model_worker.controls.effective_controls", controls)
    monkeypatch.setattr("scryntic.research.baseline_scores.evaluate_algorithm", fit)
    with pytest.raises(IsolationError, match="missing effective controls"):
        main()
    assert reached == ["controls"]


def test_baseline_profile_retains_sandbox_without_runtime_mounts() -> None:
    BASELINE_CPU.require_owned()
    assert BASELINE_CPU.cpu_seconds == 90
    assert BASELINE_CPU.wall_seconds == 180
    assert not BASELINE_CPU.allow_threads
    assert "/model" not in BASELINE_CPU.readonly
    assert "PrivateNetwork=yes" in BASELINE_CPU.properties


def test_probe_is_bound_and_cannot_be_accepted_as_a_fitted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BaselineJob("zero", EXPECTED_INPUT_SHA256, 100, 100 + 180 * 10**9)
    attempt = DurableAttempt(job, 1, "a" * 32)
    worker = CPUWorker()
    worker._baseline_active = worker._baseline_probe = True
    seen = []

    def verify(controls: object, host: object, profile: object) -> None:
        seen.append(profile)

    monkeypatch.setattr("scryntic.model_worker.launcher.verify_controls", verify)
    probe: dict[str, Any] = {
        "schema": "scryntic.pi03.worker-probe.v1",
        "job_sha256": job.sha256,
        "input_sha256": job.input_sha256,
        "model_fitted": False,
        "final_labels_accessed": False,
    }
    frame: dict[str, Any] = {
        "version": 3,
        "controls": {},
        "probe": probe,
        "usage": {"elapsed_ns": 1, "cpu_ns": 1, "max_rss_kib": 1},
    }
    data = worker._decode_worker(canonical_json_bytes(frame), {}, attempt)
    assert json.loads(data) == probe
    assert seen == [BASELINE_CPU]
    with pytest.raises(ValueError):
        decode_response(data, attempt)
    probe["model_fitted"] = 0
    mismatched_frame = canonical_json_bytes(frame)
    with pytest.raises(IsolationError):
        worker._decode_worker(mismatched_frame, {}, attempt)


def test_code_drift_has_a_new_identity_and_is_denied_before_input_access() -> None:
    job = BaselineJob("zero", EXPECTED_INPUT_SHA256, 100, 100 + 180 * 10**9)
    changed = replace(job, workload_sha256="a" * 64)
    assert changed.job_id != job.job_id
    assert changed.sha256 != job.sha256
    worker = CPUWorker()
    attempt = DurableAttempt(changed, 1, "a" * 32)
    with pytest.raises(IsolationError, match="inventory changed"):
        worker._verify_baseline(attempt, b"")
