"""Pinned F18 inputs to immutable, size-bounded canonical forecast reports."""

import platform
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import MAX_INSPECT_ROWS, MAX_RESULT_BYTES
from scryntic.dataset.schemas import EVOLVED_DATASET_SCHEMA
from scryntic.dataset.service import DatasetService
from scryntic.domain.dataset import DatasetRef
from scryntic.domain.validation import digest, identifier
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.evaluation import evaluate
from scryntic.replay.reader import MAX_REPLAY_ROWS, CandleReplayReader


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    canonical_bytes: bytes

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()


class ReplayService:
    def __init__(
        self,
        datasets: DatasetService,
        *,
        code_revision: str,
        dependency_lock_sha256: str,
    ) -> None:
        identifier(code_revision)
        digest(dependency_lock_sha256)
        self._datasets = datasets
        self._environment = {
            "code_revision": code_revision,
            "dependency_lock_sha256": dependency_lock_sha256,
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        }

    def evaluate(self, reference: DatasetRef, config: ReplayConfig) -> EvaluationReport:
        if (
            reference.schema != EVOLVED_DATASET_SCHEMA
            or reference.row_count > MAX_REPLAY_ROWS
        ):
            raise ValueError("Replay requires bounded F18 schema 1.1")
        pins = self._datasets.pins(reference)
        manifest = self._datasets.read_manifest(reference)
        rows: list[dict[str, Any]] = []
        # Every page is independently inspected inside the existing Linux profile.
        for offset in range(0, reference.row_count, MAX_INSPECT_ROWS):
            rows.extend(self._datasets.inspect(reference, offset=offset))
        # Empty snapshots still validate the native artifact in the restricted worker.
        if not reference.row_count:
            self._datasets.inspect(reference)
        report = evaluate(CandleReplayReader(manifest, rows, config))
        report["environment"] = self._environment
        report["dataset"] = {
            "manifest_sha256": reference.manifest_sha256,
            "parquet_sha256": manifest["parquet"]["sha256"],
            "provenance": manifest,
            "pins": pins,
        }
        data = canonical_json_bytes(report)
        if len(data) > MAX_RESULT_BYTES:
            raise ValueError("Evaluation report exceeds analytical byte limit")
        return EvaluationReport(data)
