"""Subprocess fixture for an interruption while constructor awaits readiness."""

import os
import sys
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scryntic.configuration.paths import Installation
from scryntic.ingestion.sqlite_spool import DurableIngestor


def installation(root: Path) -> Installation:
    config = root / "config"
    state = root / "state"
    runtime = root / "runtime"
    credentials = config / "credentials"
    for path in (config, state, runtime, credentials):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    uid = os.geteuid()
    return Installation(config, state, runtime, credentials, uid, uid, False)


def main(root: Path) -> None:
    target = installation(root)

    def interrupted(self: Future[Any], timeout: float | None = None) -> Any:
        del self, timeout
        raise KeyboardInterrupt("injected readiness interruption")

    with patch.object(Future, "result", interrupted):
        try:
            DurableIngestor(
                target,
                producer="collector-a",
                epoch="boot-a",
                capacity=1,
                max_payload_bytes=1024,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise RuntimeError("startup interruption was not propagated")

    with DurableIngestor(
        target,
        producer="collector-a",
        epoch="boot-b",
        capacity=1,
        max_payload_bytes=1024,
    ):
        pass
    os.write(sys.stdout.fileno(), b"released\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
