"""Exit at real SQL boundaries without cleanup; invoked only in subprocess tests."""

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scryntic.normalization.runner import process_next
from scryntic.normalization.sqlite_store import NormalizationStore
from tests.normalization.helpers import (
    INSTRUMENT_ID,
    NORMALIZED_AT_NS,
    FixedRawReader,
    installation,
    instrument,
    recovery_records,
)

_AFTER_STATEMENTS = {
    "after_observation_insert": ("INSERT INTO candle_observations",),
    "after_outcome_insert": ("INSERT INTO processing_outcomes",),
    "after_checkpoint_write": (
        "INSERT INTO processing_checkpoint",
        "UPDATE processing_checkpoint",
    ),
    "after_barrier_delete": ("DELETE FROM processing_barrier",),
    "after_outcome_commit": ("COMMIT",),
    "after_barrier_insert": ("INSERT INTO processing_barrier",),
    "after_barrier_commit": ("COMMIT",),
}
_BARRIER_STAGES = {
    "after_barrier_insert",
    "before_barrier_commit",
    "after_barrier_commit",
}


class CrashConnection(sqlite3.Connection):
    stage: str | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if self.stage == "before_begin" and sql == "BEGIN IMMEDIATE":
            os._exit(86)
        if (
            self.stage in ("before_outcome_commit", "before_barrier_commit")
            and sql == "COMMIT"
        ):
            os._exit(86)
        cursor = super().execute(sql, parameters)
        if self.stage is not None and sql.startswith(
            _AFTER_STATEMENTS.get(self.stage, ())
        ):
            os._exit(86)
        return cursor


def main(root: Path, stage: str) -> None:
    if stage not in _AFTER_STATEMENTS and stage not in {
        "before_begin",
        "before_outcome_commit",
        "before_barrier_commit",
    }:
        raise ValueError("Unknown crash stage")
    real_connect = sqlite3.connect
    connections: list[CrashConnection] = []

    def connect(database: Path, **kwargs: Any) -> sqlite3.Connection:
        assert not connections, "Crash harness must use one production connection"
        connection = real_connect(database, factory=CrashConnection, **kwargs)
        connections.append(connection)
        return connection

    with patch("sqlite3.connect", connect):
        with NormalizationStore(installation(root), producer="collector-a") as store:
            # Initialization has completed; arm only the next record transaction.
            connections[0].stage = stage
            metadata = {} if stage in _BARRIER_STAGES else {INSTRUMENT_ID: instrument()}
            process_next(
                FixedRawReader(recovery_records()),
                store,
                metadata,
                normalized_at_ns=NORMALIZED_AT_NS,
            )
    raise RuntimeError("Requested crash stage was not reached")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
