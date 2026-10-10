"""Kill the real foreground process at publication durability checkpoints."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.daemon.test_config import installation

_SCRIPT = """
import asyncio
import os
import signal
import sqlite3
import sys
import threading
from pathlib import Path
from scryntic.daemon.components import Services
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.foreground import run_foreground
from tests.daemon.test_config import installation
from scryntic.ingestion.sqlite_spool import DurableIngestor
accepting = False
original_accept = DurableIngestor._accept
original_connect = sqlite3.connect
kind = sys.argv[4] if len(sys.argv) > 4 else "forced"
stopped = False
def stop_point():
    global stopped
    if stopped:
        return
    stopped = True
    os.kill(os.getpid(), signal.SIGTERM if kind == "graceful" else signal.SIGKILL)
class CrashConnection(sqlite3.Connection):
    def execute(self, statement, parameters=(), /):
        raw_commit = accepting and threading.current_thread().name == "scryntic-ingestion-writer" and statement == "COMMIT"
        if raw_commit and sys.argv[2] == "before_raw_commit":
            stop_point()
        result = super().execute(statement, parameters)
        if raw_commit and sys.argv[2] == "after_raw_commit":
            stop_point()
        return result
def connect(*args, **kwargs):
    kwargs["factory"] = CrashConnection
    return original_connect(*args, **kwargs)
def accept(self, connection, envelope):
    global accepting
    accepting = True
    try:
        return original_accept(self, connection, envelope)
    finally:
        accepting = False
if sys.argv[2] in ("before_raw_commit", "after_raw_commit"):
    sqlite3.connect = connect
    DurableIngestor._accept = accept
def checkpoint(stage):
    if stage == sys.argv[2]:
        stop_point()
config = DaemonConfig(installation(Path(sys.argv[1])), profile="fixture", pipeline_interval_s=0.01)
if sys.argv[2] != "recover":
    raise SystemExit(run_foreground(Services(config, publication_fault=checkpoint)))
async def recover():
    services = Services(config)
    services.stop_intake()
    await services.start()
    committed = services.health().durably_committed
    await services.drain()
    health = services.health()
    await services.close()
    assert committed == 0 if sys.argv[3] == "before_raw_commit" and kind == "forced" else 1 <= committed <= 2
    assert health.durably_committed == committed
    assert health.normalized == committed
    assert health.published == committed
asyncio.run(recover())
"""


@pytest.mark.parametrize(
    "checkpoint",
    [
        "before_raw_commit",
        "after_raw_commit",
        "after_reservation_commit",
        "after_raw_object",
        "after_normalized_object",
        "after_partition_views",
        "after_prepare_commit",
        "after_catalog_commit",
    ],
)
@pytest.mark.parametrize("kind", ["forced", "graceful"])
def test_sigkill_publication_checkpoint_recovers_without_skipping_raw(
    tmp_path: Path, checkpoint: str, kind: str
) -> None:
    installation(tmp_path)
    command = [sys.executable, "-c", _SCRIPT, str(tmp_path)]
    environment = {**os.environ, "PYTHONPATH": "src:."}
    crashed = subprocess.run(
        [*command, checkpoint, checkpoint, kind],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
    assert crashed.returncode == (0 if kind == "graceful" else -9), crashed.stderr
    recovered = subprocess.run(
        [*command, "recover", checkpoint, kind],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert "Traceback" not in recovered.stderr
