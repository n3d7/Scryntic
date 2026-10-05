"""Bounded no-replace JSON artifact for a validated local forecast."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from scryntic.application.analysis import ForecastArtifactRef, ValidatedForecast
from scryntic.archive.canonical import JsonValue, canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.identity import SchemaRef, Version
from scryntic.jobs.result import AdmittedForecast

ARTIFACT_SCHEMA = SchemaRef("scryntic.forecast-result", Version(1, 0))
MAX_ARTIFACT_BYTES = 1_000_000


def _schema(value: SchemaRef) -> dict[str, object]:
    return {
        "name": value.name,
        "major": value.version.major,
        "minor": value.version.minor,
    }


def _payload(value: ValidatedForecast) -> dict[str, object]:
    request = value.request
    descriptor = value.descriptor
    model = descriptor.model
    result = value.result
    dataset = request.dataset
    payload: dict[str, object] = {
        "schema": _schema(ARTIFACT_SCHEMA),
        "dataset": {
            "manifest_sha256": dataset.manifest_sha256,
            "schema": _schema(dataset.schema),
            "row_count": dataset.row_count,
        },
        "verified_dataset": {
            "last_start_ns": value.dataset.last_start_ns,
            "interval_ns": value.dataset.interval_ns,
        },
        "request": {
            "request_id": request.request_id,
            "target": request.target,
            "frequency_ns": request.frequency_ns,
            "horizon": request.horizon,
            "covariates": list(request.covariates),
            "allow_remote": request.allow_remote,
        },
        "provider": {
            "provider_id": descriptor.provider_id,
            "execution": descriptor.execution,
            "contract_version": {
                "major": descriptor.contract_version.major,
                "minor": descriptor.contract_version.minor,
            },
            "capabilities": sorted(descriptor.capabilities),
            "max_rows": descriptor.max_rows,
            "max_horizon": descriptor.max_horizon,
            "input_schemas": [_schema(schema) for schema in descriptor.input_schemas],
            "model": {
                "origin": model.origin,
                "publisher": model.publisher,
                "revision": model.revision,
                "license_id": model.license_id,
                "usage_policy_id": model.usage_policy_id,
                "loading_requirements": list(model.loading_requirements),
                "artifact_sha256": list(model.artifact_sha256),
            },
        },
        "result": {
            "request_id": result.request_id,
            "provider_id": result.provider_id,
            "model_revision": result.model_revision,
            "points": [
                {"timestamp_ns": point.timestamp_ns, "value": repr(point.value)}
                for point in result.points
            ],
            "value_encoding": "finite-float-repr-v1",
        },
        "parameters": {},
        "seed": None,
        "hardware": "none; trusted deterministic fake provider",
    }
    if isinstance(value, AdmittedForecast):
        payload["job"] = {
            "request_sha256": value.job.sha256,
            "review_sha256": value.job.review.sha256,
            "policy_sha256": value.job.policy.sha256,
            "intended_use": value.job.intended_use,
        }
        payload["determinism"] = {
            "seed": value.job.seed,
            "hardware": value.job.review.loading.device,
            "runtime": value.job.review.loading.runtime,
            "limitations": "trusted deterministic F20 fixture only; no real model qualification",
        }
    return payload


class ImmutableForecastStore:
    """Private local JSON storage addressed by its canonical SHA-256."""

    def __init__(self, installation: Installation) -> None:
        self._installation = installation
        self.root = installation.state_dir / "forecasts"

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._installation.owner_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("Unsafe forecast artifact directory")

    def _prepare(self) -> Path:
        with directory(
            self._installation.state_dir,
            self._installation.owner_uid,
            private=True,
        ):
            self._ensure_directory(self.root)
            staging = self.root / "staging"
            self._ensure_directory(staging)
            return staging

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_file(self, path: Path) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._installation.owner_uid
                or not 0 < info.st_size <= MAX_ARTIFACT_BYTES
            ):
                raise ValueError("Invalid forecast artifact object")
            chunks = bytearray()
            while part := os.read(
                fd, min(65_536, MAX_ARTIFACT_BYTES + 1 - len(chunks))
            ):
                chunks.extend(part)
                if len(chunks) > MAX_ARTIFACT_BYTES:
                    raise ValueError("Forecast artifact exceeds bound")
            if len(chunks) != info.st_size:
                raise ValueError("Forecast artifact changed during read")
            return bytes(chunks)
        finally:
            os.close(fd)

    def _path(self, digest: str) -> Path:
        return self.root / "objects" / "sha256" / digest[:2] / f"{digest}.json"

    def write(self, value: ValidatedForecast) -> ForecastArtifactRef:
        if not isinstance(value, ValidatedForecast):
            raise TypeError("Expected validated forecast")
        value.validate()
        data = canonical_json_bytes(cast(JsonValue, _payload(value)))
        if not 0 < len(data) <= MAX_ARTIFACT_BYTES:
            raise ValueError("Forecast artifact exceeds bound")
        digest = sha256(data).hexdigest()
        staging = self._prepare()
        fd, name = tempfile.mkstemp(prefix=".forecast-", suffix=".json", dir=staging)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o400, follow_symlinks=False)
            folder = self.root / "objects"
            self._ensure_directory(folder)
            folder = folder / "sha256"
            self._ensure_directory(folder)
            folder = folder / digest[:2]
            self._ensure_directory(folder)
            target = self._path(digest)
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                if self._read_file(target) != data:
                    raise ValueError(
                        "Conflicting immutable forecast artifact"
                    ) from None
            self._fsync_directory(folder)
        finally:
            temporary.unlink(missing_ok=True)
            self._fsync_directory(staging)
        return ForecastArtifactRef(
            digest,
            value.request.dataset,
            value.descriptor.provider_id,
            value.descriptor.model.revision,
        )

    def read(self, reference: ForecastArtifactRef) -> dict[str, Any]:
        if not isinstance(reference, ForecastArtifactRef):
            raise TypeError("Expected forecast artifact reference")
        data = self._read_file(self._path(reference.sha256))
        if sha256(data).hexdigest() != reference.sha256:
            raise ValueError("Forecast artifact digest disagrees")
        payload = json.loads(data)
        if not isinstance(payload, dict) or canonical_json_bytes(payload) != data:
            raise ValueError("Invalid canonical forecast artifact")
        dataset = payload.get("dataset")
        provider = payload.get("provider")
        result = payload.get("result")
        if (
            payload.get("schema") != _schema(ARTIFACT_SCHEMA)
            or not isinstance(dataset, dict)
            or dataset.get("manifest_sha256") != reference.dataset.manifest_sha256
            or dataset.get("schema") != _schema(reference.dataset.schema)
            or dataset.get("row_count") != reference.dataset.row_count
            or not isinstance(provider, dict)
            or provider.get("provider_id") != reference.provider_id
            or not isinstance(provider.get("model"), dict)
            or provider["model"].get("revision") != reference.model_revision
            or not isinstance(result, dict)
            or result.get("provider_id") != reference.provider_id
            or result.get("model_revision") != reference.model_revision
        ):
            raise ValueError("Forecast artifact reference disagrees")
        return payload
