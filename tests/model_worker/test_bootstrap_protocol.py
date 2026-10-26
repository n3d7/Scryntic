"""Closed worker protocol tests; all execution and host controls are stubbed."""

import io
import json
import sys
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.jobs.durable import DurableAttempt
from scryntic.model_worker import bootstrap
from scryntic.model_worker.profile import BASELINE_CPU, WIRE_BYTES, IsolationError
from scryntic.research import baseline_jobs, baseline_scores, baselines
from scryntic.research.baseline_jobs import BaselineJob, encode_attempt


@pytest.fixture(autouse=True)
def forbid_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("A protocol test reached a real execution or host-control boundary")

    monkeypatch.setattr(baseline_scores, "evaluate_algorithm", forbidden)
    monkeypatch.setattr(bootstrap, "_forecast", forbidden)
    monkeypatch.setattr("scryntic.imports.bootstrap.restrict_syscalls", forbidden)
    monkeypatch.setattr("scryntic.model_worker.controls.effective_controls", forbidden)


@pytest.fixture
def baseline_request(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes]:
    data = b'{"synthetic-fixture":true}'
    # Keep real attempt encoding/decoding, with a small test-only input pin.
    input_sha256 = sha256(data).hexdigest()
    monkeypatch.setattr(baseline_jobs, "EXPECTED_INPUT_SHA256", input_sha256)
    monkeypatch.setattr(baseline_jobs, "workload_identity", lambda: "c" * 64)
    attempt = DurableAttempt(
        BaselineJob("zero", input_sha256, 100, 200, workload_sha256="c" * 64),
        1,
        "a" * 32,
    )
    request = {
        "baseline_attempt": canonical_json_bytes(encode_attempt(attempt)).decode(),
        "baseline_input": data.decode(),
        "host": {"uid": 1000},
        "mode": "execute",
    }
    return request, attempt, data


@pytest.fixture
def measured_usage(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    monkeypatch.setattr(
        "scryntic.model_worker.bootstrap.time.monotonic_ns", lambda: 600
    )
    monkeypatch.setattr(
        "scryntic.model_worker.bootstrap.resource.getrusage",
        lambda who: SimpleNamespace(ru_utime=0.25, ru_stime=0.125, ru_maxrss=2048),
    )
    return {"elapsed_ns": 500, "cpu_ns": 375_000_000, "max_rss_kib": 2048}


@pytest.mark.parametrize("kind", ["synthetic", "model", "baseline"])
def test_closed_request_selects_only_its_registered_workload(kind: str) -> None:
    request: dict[str, Any] = {"attempt": "{}", "host": {}, "mode": "execute"}
    if kind == "model":
        request.update(model="timesfm-2.5", context={})
    elif kind == "baseline":
        request = {
            "baseline_attempt": "{}",
            "baseline_input": "{}",
            "host": {},
            "mode": "execute",
        }
    decoded, model, baseline = bootstrap._read_request(canonical_json_bytes(request))
    assert decoded == request
    assert baseline is (kind == "baseline")
    if kind == "model":
        assert model.name == "timesfm-2.5"
        assert model.adapter is not None
    else:
        assert model is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{}",
        b"[]",
        b"\xff",
        b'{"attempt":"{}","host":{},"mode":"execute","mode":"probe"}',
        b'{ "attempt":"{}","host":{},"mode":"execute"}',
        canonical_json_bytes({"attempt": "{}", "host": {}}),
        canonical_json_bytes(
            {"attempt": "{}", "host": {}, "mode": "execute", "shell": "true"}
        ),
    ],
)
def test_request_rejects_noncanonical_or_open_shapes(data: bytes) -> None:
    with pytest.raises(IsolationError, match="Invalid bounded worker message"):
        bootstrap._read_request(data)


@pytest.mark.parametrize("name", ["fake-persistence", "unregistered-model"])
def test_model_shape_cannot_select_a_synthetic_or_unknown_adapter(name: str) -> None:
    request: dict[str, Any] = {
        "attempt": "{}",
        "host": {},
        "mode": "execute",
        "model": name,
        "context": {},
    }
    data = canonical_json_bytes(request)
    with pytest.raises((IsolationError, ValueError)):
        bootstrap._read_request(data)


def test_only_baseline_shape_receives_the_larger_request_bound() -> None:
    request: dict[str, Any] = {
        "baseline_attempt": "{}",
        "baseline_input": "x" * WIRE_BYTES,
        "host": {},
        "mode": "execute",
    }
    data = canonical_json_bytes(request)
    assert len(data) > WIRE_BYTES
    assert bootstrap._read_request(data) == (request, None, True)
    synthetic: dict[str, Any] = {
        "attempt": "x" * WIRE_BYTES,
        "host": {},
        "mode": "execute",
    }
    synthetic_data = canonical_json_bytes(synthetic)
    with pytest.raises(IsolationError):
        bootstrap._read_request(synthetic_data)
    request["baseline_input"] = "x" * baselines.REQUEST_BYTES
    oversized_data = canonical_json_bytes(request)
    with pytest.raises(IsolationError):
        bootstrap._read_request(oversized_data)


@pytest.mark.parametrize(
    ("model", "baseline", "version"),
    [(None, False, 1), (object(), False, 2), (None, True, 3), (object(), True, 3)],
)
def test_response_versions_and_usage_are_bound_to_workload_kind(
    model: object | None,
    baseline: bool,
    version: int,
    measured_usage: dict[str, int],
) -> None:
    message = bootstrap._response_message(
        b"{}", {"checked": True}, model, baseline, None, 100
    )
    expected: dict[str, Any] = {
        "version": version,
        "controls": {"checked": True},
        "response": "{}",
    }
    if version != 1:
        expected["usage"] = measured_usage
    assert message == expected


def test_baseline_response_keeps_the_usage_bound_into_its_result(
    measured_usage: dict[str, int],
) -> None:
    bound_usage = {"elapsed_ns": 17, "cpu_ns": 11, "max_rss_kib": 23}
    message = bootstrap._response_message(b"{}", {}, None, True, bound_usage, 100)
    assert message["usage"] == bound_usage
    assert message["usage"] != measured_usage
    with pytest.raises(UnicodeDecodeError):
        bootstrap._response_message(b"\xff", {}, None, True, bound_usage, 100)


@pytest.mark.parametrize(
    ("changed", "error", "message"),
    [
        ("input", IsolationError, "Fixed baseline input changed"),
        ("workload", IsolationError, "Fixed baseline application inventory changed"),
        ("algorithm", ValueError, "Unregistered PI03 fixed job"),
        ("shape", ValueError, "Invalid PI03 attempt"),
    ],
)
def test_baseline_rejects_changed_binding_or_unregistered_attempt_before_execution(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    changed: str,
    error: type[Exception],
    message: str,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    request, _, _ = baseline_request
    stage = [75]
    raw = json.loads(request["baseline_attempt"])
    if changed == "input":
        request["baseline_input"] += " "
    elif changed == "workload":
        raw["job"]["workload_sha256"] = "d" * 64
        # Inventory admission must fail before checking even a changed input.
        request["baseline_input"] += " "
    elif changed == "algorithm":
        raw["job"]["algorithm"] = "unregistered-algorithm"
    else:
        raw["extra"] = True
    request["baseline_attempt"] = canonical_json_bytes(raw).decode()
    with pytest.raises(error, match=message):
        bootstrap._baseline_result(request, {}, "execute", 100, stage)
    assert stage == [75]
    assert capfdbinary.readouterr().out == b""


@pytest.mark.parametrize("value", [None, 1, b"{}", []])
def test_baseline_input_must_be_a_string_before_attempt_decoding(value: Any) -> None:
    with pytest.raises(IsolationError, match="Unexpected fixed baseline operation"):
        bootstrap._baseline_result(
            {"baseline_input": value, "baseline_attempt": "not-json"},
            {},
            "execute",
            100,
            [75],
        )


def test_probe_validates_bound_input_and_emits_no_fitted_response(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    measured_usage: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    request, attempt, data = baseline_request
    validated: list[bytes] = []
    monkeypatch.setattr(baselines, "validate_input", validated.append)
    stage = [75]
    assert (
        bootstrap._baseline_result(
            request, {"checked": True}, "baseline-probe", 100, stage
        )
        is None
    )
    assert validated == [data]
    assert stage == [75]
    assert json.loads(capfdbinary.readouterr().out) == {
        "version": 3,
        "controls": {"checked": True},
        "probe": {
            "schema": "scryntic.pi03.worker-probe.v1",
            "job_sha256": attempt.job.sha256,
            "input_sha256": attempt.job.input_sha256,
            "model_fitted": False,
            "final_labels_accessed": False,
        },
        "usage": measured_usage,
    }


def test_probe_validation_failure_emits_nothing_and_never_evaluates(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    request, _, data = baseline_request

    def reject(value: bytes) -> None:
        assert value == data
        raise ValueError("synthetic admission denied")

    monkeypatch.setattr(baselines, "validate_input", reject)
    stage = [75]
    with pytest.raises(ValueError, match="synthetic admission denied"):
        bootstrap._baseline_result(request, {}, "baseline-probe", 100, stage)
    assert stage == [75]
    assert capfdbinary.readouterr().out == b""


def test_execution_frames_stub_result_with_exact_attempt_controls_and_usage(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    measured_usage: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, attempt, data = baseline_request
    evaluated: list[tuple[bytes, str]] = []

    def evaluate(value: bytes, algorithm: str) -> dict[str, Any]:
        evaluated.append((value, algorithm))
        return {"synthetic-test-result": True}

    monkeypatch.setattr(baseline_scores, "evaluate_algorithm", evaluate)
    controls: dict[str, Any] = {"checked": True}
    stage = [75]
    result = bootstrap._baseline_result(request, controls, "execute", 100, stage)
    assert result is not None
    response, usage = result
    assert evaluated == [(data, "zero")]
    assert usage == measured_usage
    assert stage == [76]
    assert json.loads(response) == {
        "schema": "scryntic.pi03.baseline-response.v1",
        "job_sha256": attempt.job.sha256,
        "attempt": attempt.number,
        "token": attempt.token,
        "result": {
            "synthetic-test-result": True,
            "workload_sha256": attempt.job.workload_sha256,
            "resource_usage": measured_usage,
            "controls_sha256": sha256(canonical_json_bytes(controls)).hexdigest(),
        },
    }


def test_main_probe_returns_after_one_validated_probe_without_result_envelope(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    measured_usage: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    request, attempt, data = baseline_request
    request["mode"] = "baseline-probe"
    encoded = canonical_json_bytes(request)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.BytesIO(encoded))
    stage = [0]
    reached: list[str] = []

    def restrict(**kwargs: Any) -> None:
        assert stage == [72]
        assert kwargs == {}
        reached.append("syscalls")

    def controls(
        host: dict[str, Any], profile: object, **kwargs: Any
    ) -> dict[str, Any]:
        assert stage == [73]
        assert profile is BASELINE_CPU
        reached.append("controls")
        return {"checked": True}

    def validate(value: bytes) -> None:
        assert stage == [75]
        assert value == data
        reached.append("validate")

    monkeypatch.setattr("scryntic.imports.bootstrap.restrict_syscalls", restrict)
    monkeypatch.setattr("scryntic.model_worker.controls.effective_controls", controls)
    monkeypatch.setattr(baselines, "validate_input", validate)
    bootstrap.main(stage)
    assert stage == [75]
    assert reached == ["syscalls", "controls", "validate"]
    frame = json.loads(capfdbinary.readouterr().out)
    assert set(frame) == {"version", "controls", "probe", "usage"}
    assert frame["version"] == 3
    assert frame["controls"] == {"checked": True}
    assert frame["probe"]["job_sha256"] == attempt.job.sha256
    assert frame["probe"]["model_fitted"] is False
    assert frame["probe"]["final_labels_accessed"] is False
    assert frame["usage"] == measured_usage | {"elapsed_ns": 0}


@pytest.mark.parametrize("mode", ["probe", "cpu", "hold", "../execute"])
def test_main_rejects_unregistered_baseline_modes_after_controls_before_dispatch(
    baseline_request: tuple[dict[str, Any], DurableAttempt[BaselineJob], bytes],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    request, _, _ = baseline_request
    request["mode"] = mode
    data = canonical_json_bytes(request)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.BytesIO(data))
    stage = [0]
    reached: list[str] = []

    def restrict(**kwargs: Any) -> None:
        assert stage == [72]
        assert kwargs == {}
        reached.append("syscalls")

    def controls(
        host: dict[str, Any], profile: object, **kwargs: Any
    ) -> dict[str, Any]:
        assert stage == [73]
        assert kwargs == {"stage": stage}
        assert host == request["host"]
        assert profile is BASELINE_CPU
        reached.append("controls")
        return {"checked": True}

    def dispatch(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Unregistered mode reached baseline dispatch")

    monkeypatch.setattr("scryntic.imports.bootstrap.restrict_syscalls", restrict)
    monkeypatch.setattr("scryntic.model_worker.controls.effective_controls", controls)
    monkeypatch.setattr(bootstrap, "_baseline_result", dispatch)
    with pytest.raises(IsolationError, match="Unexpected fixed baseline operation"):
        bootstrap.main(stage)
    assert stage == [74]
    assert reached == ["syscalls", "controls"]
