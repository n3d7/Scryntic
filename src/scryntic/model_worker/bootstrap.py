"""Trusted preflight; arbitrary provider code starts only after controls pass."""

import os
import sys
import time
from typing import Literal


def main() -> None:
    sys.path[:] = ["/app", *sys.path]
    from scryntic.archive.canonical import canonical_json_bytes
    from scryntic.imports.bootstrap import restrict_syscalls
    from scryntic.jobs.codec import decode_attempt, encode_response
    from scryntic.model_worker.controls import effective_controls
    from scryntic.model_worker.output import read_result
    from scryntic.model_worker.profile import (
        REQUEST_PATH,
        RESULT_NAME,
        WIRE_BYTES,
        IsolationError,
    )
    from scryntic.model_worker.wire import document

    with open(REQUEST_PATH, "rb") as stream:
        request = document(stream.read(WIRE_BYTES + 1), "attempt host mode")
    restrict_syscalls()
    # Fail closed before even the trusted fixture is executed.
    controls = effective_controls(request["host"])
    from scryntic.jobs.fake import calculate

    mode = request["mode"]
    if mode == "hold":
        time.sleep(120)
        raise IsolationError("Hold probe returned")
    if mode == "cpu":
        counter = 0
        while True:
            counter = (counter + 1) % 65536
    if mode not in ("execute", "probe", "symlink", "hardlink", "flood", "crash"):
        raise IsolationError("Unknown fixed worker operation")
    if mode == "crash":
        os._exit(1)
    if mode == "flood":
        while True:
            os.write(1, b"x" * 4096)
    attempt = decode_attempt(request["attempt"].encode("utf-8"))
    revision = attempt.job.review.descriptor.model.revision
    algorithm: Literal["persistence", "trend"] = (
        "trend" if revision == "f20-fixed-trend-v1" else "persistence"
    )
    response = encode_response(
        attempt, calculate(attempt, attempt.job.review, algorithm)
    )
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
    os.write(
        1,
        canonical_json_bytes(
            {
                "version": 1,
                "controls": controls,
                "response": data.decode("utf-8"),
            }
        ),
    )


if __name__ == "__main__":
    exit_code = 1
    try:
        main()
        exit_code = 0
    finally:
        # Includes BaseException/native cleanup paths; never print diagnostics.
        os._exit(exit_code)
