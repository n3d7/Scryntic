"""Fixed unprivileged foreground host for finite F23 collection operations."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from scryntic.application.local_operations import BackfillRequest, CollectRequest
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
)
from scryntic.application.sources import (
    BYBIT_CANDLE_SCHEMA,
    HistoryRequest,
    StreamRequest,
)
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation
from scryntic.configuration.values import Capability
from scryntic.daemon.config import DaemonConfig
from scryntic.daemon.foreground import run_foreground
from scryntic.daemon.health import Fault, HealthSnapshot, State
from scryntic.domain.identity import InstrumentId
from scryntic.workflows.wire import encode_result, result

if TYPE_CHECKING:
    from scryntic.daemon.components import Services

MAX_REQUEST_BYTES = 65536
FiniteRequest = CollectRequest | BackfillRequest


def encode_request(installation: Installation, request: FiniteRequest) -> bytes:
    target = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(installation).items()
    }
    raw = json.dumps(
        {
            "version": 1,
            "installation": target,
            "operation": request.operation.value,
            "request": asdict(request),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ApplicationError(ErrorCode.INVALID)
    return raw


def decode_request(raw: bytes) -> tuple[Installation, FiniteRequest]:
    if not 0 < len(raw) <= MAX_REQUEST_BYTES:
        raise ValueError("Unbounded collection request")
    value = json.loads(raw)
    if (
        type(value) is not dict
        or set(value) != {"version", "installation", "operation", "request"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Invalid collection descriptor")
    installation = _decode_installation(value["installation"])
    fields = value["request"]
    if type(fields) is not dict:
        raise ValueError("Invalid collection request fields")
    kind: type[CollectRequest] | type[BackfillRequest]
    if value["operation"] == "collect":
        kind = CollectRequest
    elif value["operation"] == "backfill":
        kind = BackfillRequest
    else:
        raise ValueError("Unsupported collection operation")
    if set(fields) != set(kind.__dataclass_fields__):
        raise ValueError("Unknown collection request fields")
    return installation, kind(**fields)


def _decode_installation(target: object) -> Installation:
    paths = {"config_dir", "state_dir", "runtime_dir", "credential_dir"}
    if type(target) is not dict:
        raise ValueError("Invalid collection owner")
    document = cast(dict[str, Any], target)
    if (
        set(document) != paths | {"owner_uid", "config_uid", "service_mode"}
        or type(document["owner_uid"]) is not int
        or document["owner_uid"] != os.geteuid()
        or document["owner_uid"] <= 0
        or type(document["config_uid"]) is not int
        or type(document["service_mode"]) is not bool
        or any(type(document[name]) is not str for name in paths)
    ):
        raise ValueError("Invalid collection owner")
    for name in paths:
        path = Path(document[name])
        if not path.is_absolute() or ".." in path.parts or len(path.parts) > 128:
            raise ValueError("Invalid collection path")
        document[name] = path
    installation = Installation(**document)
    if installation.service_mode:
        if installation != Installation.service(os.geteuid()):
            raise ValueError("Invalid service installation")
    elif installation.config_uid != os.geteuid():
        raise ValueError("Invalid configuration owner")
    return installation


class _Finite:
    def __init__(self, services: Services, request: FiniteRequest) -> None:
        self.services = services
        self.request = request
        self._timer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.services.start()
        self._timer = asyncio.create_task(self._finish())

    async def _finish(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.request.seconds
        while asyncio.get_running_loop().time() < deadline:
            if (
                isinstance(self.request, BackfillRequest)
                and self.services.source_finished
            ):
                break
            await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    def stop_intake(self) -> None:
        self.services.stop_intake()
        if self._timer is not None:
            self._timer.cancel()

    async def drain(self) -> None:
        if self._timer is not None:
            await asyncio.gather(self._timer, return_exceptions=True)
        await self.services.drain()

    async def close(self) -> None:
        await self.services.close()

    def health(self) -> HealthSnapshot:
        return self.services.health()


def run_collection(
    installation: Installation,
    request: FiniteRequest,
    *,
    services_factory: Callable[..., Services] | None = None,
) -> ApplicationResult:
    configuration = load_configuration(installation)
    configuration.required_reference(Capability.PUBLIC_COLLECTION)
    if services_factory is None:
        from scryntic.daemon.components import Services

        services_factory = Services
    config = DaemonConfig(
        installation,
        producer=request.producer,
        epoch=request.epoch,
        profile=request.profile,
        category=request.category,
        symbol=request.symbol,
    )
    history = None
    if isinstance(request, BackfillRequest):
        history = HistoryRequest(
            StreamRequest(
                BYBIT_CANDLE_SCHEMA,
                InstrumentId("bybit", request.category, request.symbol),
            ),
            request.start_ns,
            request.end_ns,
            4,
        )
    services = services_factory(config, history_request=history)
    if run_foreground(_Finite(services, request)) != 0:
        raise ApplicationError(ErrorCode.INCOMPLETE)
    return collection_result(services, request)


def collection_result(
    services: Services, request: CollectRequest | BackfillRequest
) -> ApplicationResult:
    health = services.health()
    faults = (
        services.backfill_faults
        if isinstance(request, BackfillRequest)
        else services.collection_faults
    )
    fields: dict[str, object] = {
        "profile": request.profile,
        "producer": request.producer,
        "epoch": request.epoch,
        "received": health.received,
        "durably_committed": health.durably_committed,
        "normalized": health.normalized,
        "published": health.published,
        "shutdown_complete": health.shutdown_pending == 0
        and health.unfinished_operations == 0,
        "faults": [fault.value for fault in faults],
    }
    complete = health.state is not State.FAILED and Fault.SOURCE not in faults
    if isinstance(request, BackfillRequest):
        status, pages, repaired = services.backfill_status
        fields.update(
            {
                "start_ns": request.start_ns,
                "end_ns": request.end_ns,
                "coverage": status,
                "pages": pages,
                "repaired_candles": repaired,
            }
        )
        complete = complete and status == "complete"
    return result(request.operation, fields, complete=complete)


def main() -> int:
    try:
        installation, request = decode_request(
            sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        )
        value = run_collection(installation, request)
        sys.stdout.buffer.write(encode_result(value))
        sys.stdout.buffer.flush()
        return 0
    except ModuleNotFoundError:
        return ApplicationError(ErrorCode.UNAVAILABLE).exit_code
    except (Exception, KeyboardInterrupt):
        # Fixed host failure has no acknowledgment or untrusted exception output.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
