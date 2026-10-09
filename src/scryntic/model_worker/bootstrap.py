"""Trusted preflight; arbitrary provider code starts only after controls pass."""

import os
import resource
import sys
import time
from typing import Any, Literal


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
        REQUEST_PATH,
        SYNTHETIC_CPU,
        WIRE_BYTES,
        IsolationError,
    )
    from scryntic.model_worker.wire import document
    from scryntic.models.definitions import definition

    stage[0] = 71
    with open(REQUEST_PATH, "rb") as stream:
        data = stream.read(WIRE_BYTES + 1)
    try:
        request = document(data, "attempt host mode")
        model = None
    except IsolationError:
        request = document(data, "attempt host mode model context")
        model = definition(request["model"])
        if model.adapter is None:
            raise IsolationError("Unexpected model workload") from None
    profile = model.profile if model is not None else SYNTHETIC_CPU
    stage[0] = 72
    if profile.allow_threads:
        restrict_syscalls(allow_threads=True)
    else:
        restrict_syscalls()
    # Fail closed before even the trusted fixture is executed.
    stage[0] = 73
    controls = (
        effective_controls(request["host"], profile)
        if model is not None
        else effective_controls(request["host"])
    )
    stage[0] = 74
    mode = request["mode"]
    _probe_mode(mode, model is not None)
    attempt = decode_attempt(request["attempt"].encode("utf-8"))
    started = time.monotonic_ns()
    stage[0] = 75
    result = _forecast(attempt, request, model)
    stage[0] = 76
    data = _result_bytes(encode_response(attempt, result), mode)
    stage[0] = 77
    message: dict[str, Any] = {
        "version": 2 if model is not None else 1,
        "controls": controls,
        "response": data.decode("utf-8"),
    }
    if model is not None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        message["usage"] = {
            "elapsed_ns": time.monotonic_ns() - started,
            "cpu_ns": int((usage.ru_utime + usage.ru_stime) * 10**9),
            "max_rss_kib": usage.ru_maxrss,
        }
    os.write(1, canonical_json_bytes(message))


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
            if len(stage) == 1 and type(stage[0]) is int and 70 <= stage[0] <= 77
            else 78
        )
        if completed:
            exit_code = 0
        os._exit(exit_code)


if __name__ == "__main__":
    entrypoint()
