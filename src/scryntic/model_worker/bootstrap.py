"""Trusted preflight; arbitrary provider code starts only after controls pass."""

import os
import resource
import sys
import time
from typing import Any, Literal

_FAILURE_CODES = frozenset(
    (
        *range(70, 78),
        *range(80, 107),
        *range(112, 125),
        *range(128, 141),
        *range(144, 157),
    )
)


def main(stage: list[int] | None = None) -> None:
    # Fixed failure-stage codes only; no exception text crosses the boundary.
    stage = [70] if stage is None else stage
    stage[0] = 70
    sys.path[:] = ["/app", *sys.path]
    from scryntic.archive.canonical import canonical_json_bytes
    from scryntic.imports.bootstrap import restrict_syscalls
    from scryntic.jobs.codec import decode_attempt, encode_response
    from scryntic.model_worker.controls import effective_controls
    from scryntic.model_worker.profile import (
        BASELINE_CPU,
        REQUEST_PATH,
        SYNTHETIC_CPU,
        IsolationError,
    )
    from scryntic.research.baselines import REQUEST_BYTES

    stage[0] = 71
    with open(REQUEST_PATH, "rb") as stream:
        data = stream.read(REQUEST_BYTES + 1)
    request, model, baseline = _read_request(data)
    if baseline:
        profile = BASELINE_CPU
    elif model is not None:
        profile = model.profile
    else:
        profile = SYNTHETIC_CPU
    stage[0] = 72
    if profile.allow_threads:
        restrict_syscalls(allow_threads=True)
    else:
        restrict_syscalls()
    # Fail closed before even the trusted fixture is executed.
    stage[0] = 73
    controls = (
        effective_controls(request["host"], profile, stage=stage)
        if model is not None or baseline
        else effective_controls(request["host"], stage=stage)
    )
    stage[0] = 74
    mode = request["mode"]
    if baseline and mode not in ("execute", "baseline-probe"):
        raise IsolationError("Unexpected fixed baseline operation")
    if not baseline:
        _probe_mode(mode, model is not None)
    started = time.monotonic_ns()
    baseline_usage: dict[str, int] | None = None
    stage[0] = 75
    if baseline:
        baseline_result = _baseline_result(request, controls, mode, started, stage)
        if baseline_result is None:
            return
        data, baseline_usage = baseline_result
    else:
        attempt = decode_attempt(request["attempt"].encode("utf-8"))
        result = _forecast(attempt, request, model)
        stage[0] = 76
        data = _result_bytes(encode_response(attempt, result), mode)
    stage[0] = 77
    message = _response_message(
        data, controls, model, baseline, baseline_usage, started
    )
    os.write(1, canonical_json_bytes(message))


def _response_message(
    data: bytes,
    controls: dict[str, Any],
    model: Any,
    baseline: bool,
    baseline_usage: dict[str, int] | None,
    started: int,
) -> dict[str, Any]:
    version = 1
    if baseline:
        version = 3
    elif model is not None:
        version = 2
    message: dict[str, Any] = {
        "version": version,
        "controls": controls,
        "response": data.decode("utf-8"),
    }
    if model is not None or baseline:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        message["usage"] = (
            baseline_usage
            if baseline_usage is not None
            else {
                "elapsed_ns": time.monotonic_ns() - started,
                "cpu_ns": int((usage.ru_utime + usage.ru_stime) * 10**9),
                "max_rss_kib": usage.ru_maxrss,
            }
        )
    return message


def _read_request(data: bytes) -> tuple[dict[str, Any], Any, bool]:
    from scryntic.model_worker.profile import IsolationError
    from scryntic.model_worker.wire import document
    from scryntic.models.definitions import definition
    from scryntic.research.baselines import REQUEST_BYTES

    baseline = False
    try:
        request = document(data, "attempt host mode")
        model = None
    except IsolationError:
        try:
            request = document(data, "attempt host mode model context")
            model = definition(request["model"])
            if model.adapter is None:
                raise IsolationError("Unexpected model workload") from None
        except IsolationError:
            request = document(
                data, "baseline_attempt host mode baseline_input", maximum=REQUEST_BYTES
            )
            model = None
            baseline = True
    return request, model, baseline


def _baseline_result(
    request: dict[str, Any],
    controls: dict[str, Any],
    mode: str,
    started: int,
    stage: list[int],
) -> tuple[bytes, dict[str, int]] | None:
    import json
    from hashlib import sha256

    from scryntic.archive.canonical import canonical_json_bytes
    from scryntic.model_worker.profile import IsolationError
    from scryntic.research.baseline_jobs import decode_attempt as baseline_attempt
    from scryntic.research.baseline_jobs import encode_response as baseline_response
    from scryntic.research.baseline_scores import evaluate_algorithm

    if type(request["baseline_input"]) is not str:
        raise IsolationError("Unexpected fixed baseline operation")
    from scryntic.research.baselines import _pairs

    fixed_attempt = baseline_attempt(
        json.loads(request["baseline_attempt"], object_pairs_hook=_pairs)
    )
    fixed_input = request["baseline_input"].encode("utf-8")
    from scryntic.research.baseline_jobs import workload_identity

    if fixed_attempt.job.workload_sha256 != workload_identity():
        raise IsolationError("Fixed baseline application inventory changed")
    if sha256(fixed_input).hexdigest() != fixed_attempt.job.input_sha256:
        raise IsolationError("Fixed baseline input changed")
    if mode == "baseline-probe":
        from scryntic.research.baselines import validate_input

        validate_input(fixed_input)
        measured = resource.getrusage(resource.RUSAGE_SELF)
        os.write(
            1,
            canonical_json_bytes(
                {
                    "version": 3,
                    "controls": controls,
                    "probe": {
                        "schema": "scryntic.pi03.worker-probe.v1",
                        "job_sha256": fixed_attempt.job.sha256,
                        "input_sha256": fixed_attempt.job.input_sha256,
                        "model_fitted": False,
                        "final_labels_accessed": False,
                    },
                    "usage": {
                        "elapsed_ns": time.monotonic_ns() - started,
                        "cpu_ns": int((measured.ru_utime + measured.ru_stime) * 10**9),
                        "max_rss_kib": measured.ru_maxrss,
                    },
                }
            ),
        )
        return None
    evaluation = evaluate_algorithm(fixed_input, fixed_attempt.job.algorithm)
    evaluation["workload_sha256"] = fixed_attempt.job.workload_sha256
    measured = resource.getrusage(resource.RUSAGE_SELF)
    baseline_usage = {
        "elapsed_ns": time.monotonic_ns() - started,
        "cpu_ns": int((measured.ru_utime + measured.ru_stime) * 10**9),
        "max_rss_kib": measured.ru_maxrss,
    }
    evaluation["resource_usage"] = baseline_usage
    evaluation["controls_sha256"] = sha256(canonical_json_bytes(controls)).hexdigest()
    stage[0] = 76
    data = baseline_response(fixed_attempt, evaluation)
    return data, baseline_usage


def _probe_mode(mode: str, real: bool) -> None:
    from scryntic.model_worker.profile import IsolationError

    if mode == "hold":
        time.sleep(720 if real else 120)
        raise IsolationError("Hold probe returned")
    if mode == "cpu":
        counter = 0
        while True:
            counter = (counter + 1) % 65536
    if mode == "memory" and real:
        allocation: list[bytearray] = []
        while True:
            allocation.append(bytearray(64 * 1024**2))
    if mode not in ("execute", "probe", "symlink", "hardlink", "flood", "crash"):
        raise IsolationError("Unknown fixed worker operation")
    if mode == "crash":
        os._exit(1)
    if mode == "flood":
        while True:
            os.write(1, b"x" * 4096)


def _forecast(attempt: Any, request: dict[str, Any], model: Any) -> Any:
    from scryntic.jobs.fake import calculate
    from scryntic.model_worker.profile import IsolationError
    from scryntic.models.window import ForecastWindow

    revision = attempt.job.review.descriptor.model.revision
    algorithm: Literal["persistence", "trend"] = (
        "trend" if revision == "f20-fixed-trend-v1" else "persistence"
    )
    if model is None:
        result = calculate(attempt, attempt.job.review, algorithm)
    else:
        # Only the immutable application-owned registry chooses executable code.
        # Site hooks, user site and environment paths remain disabled by -I -S.
        sys.path.insert(1, "/runtime")
        window = ForecastWindow.from_projection(request["context"], attempt.job)
        if attempt.job.review.descriptor.model.revision != model.revision:
            raise IsolationError("Model revision changed")
        from scryntic.application.providers import ForecastPoint, ForecastResult

        assert model.adapter is not None
        values = model.adapter().infer(
            window, attempt.job.forecast.horizon, attempt.job.seed
        )
        if type(values) is not tuple or len(values) != attempt.job.forecast.horizon:
            raise IsolationError("Unbounded adapter output")
        result = ForecastResult(
            attempt.job.job_id,
            attempt.job.forecast.dataset,
            attempt.job.review.descriptor.provider_id,
            model.revision,
            tuple(
                ForecastPoint(window.starts[-1] + (i + 1) * window.frequency_ns, v)
                for i, v in enumerate(values)
            ),
        )
    return result


def _result_bytes(response: bytes, mode: str) -> bytes:
    from scryntic.model_worker.output import read_result
    from scryntic.model_worker.profile import REQUEST_PATH, RESULT_NAME

    directory = os.open("/output", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if mode == "symlink":
            os.symlink(REQUEST_PATH, RESULT_NAME, dir_fd=directory)
        else:
            descriptor = os.open(
                RESULT_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(response)
            if mode == "hardlink":
                os.link(
                    RESULT_NAME, "other", src_dir_fd=directory, dst_dir_fd=directory
                )
        data = read_result(directory)
    finally:
        os.close(directory)
    # Worker files never cross into the coordinator. The JSON is untrusted there.
    return data


def entrypoint() -> None:
    stage = [70]
    completed = False
    try:
        main(stage)
        completed = True
    finally:
        # Includes BaseException paths; never publish tracebacks or payloads.
        exit_code = (
            stage[0]
            if len(stage) == 1 and type(stage[0]) is int and stage[0] in _FAILURE_CODES
            else 78
        )
        if completed:
            exit_code = 0
        os._exit(exit_code)


if __name__ == "__main__":
    entrypoint()
