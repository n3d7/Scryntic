"""Bounded public live capture through the existing durable dataset path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
from collections.abc import Callable, Coroutine
from contextlib import ExitStack, aclosing
from pathlib import Path
from typing import Any

from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.sources import BYBIT_CANDLE_SCHEMA, StreamRequest
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.composition import _catalog_hashes, _publication_limits
from scryntic.configuration.clock import ClockLimits
from scryntic.configuration.paths import Installation, validate_directories
from scryntic.dataset.snapshot import (
    CANDLE_RECIPE_SCHEMA,
    DatasetBuilder,
    DatasetReader,
)
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.normalization.runner import Blocked, NoWork, process_next
from scryntic.normalization.sqlite_store import NormalizationStore
from scryntic.publication.coordinator import (
    NoPublishableWork,
    PublicationCoordinator,
    WaitingForNormalization,
)
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.reader import PublicationReader
from scryntic.publication.sqlite_store import PublicationStore
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource

_SYMBOL = "BTCUSDT"
_PRODUCER = "f14-public-check"


def _installation(root: Path) -> Installation:
    home = root / "home"
    home.mkdir(mode=0o700)
    installation = Installation.workstation(home=home, environment={})
    for path in (
        installation.config_dir,
        installation.state_dir,
        installation.runtime_dir,
        installation.credential_dir,
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    validate_directories(installation)
    return installation


async def _capture(
    ingestor: DurableIngestor, clock: ClockMonitor
) -> tuple[int, bool, Instrument]:
    historical = BybitHistoricalSource(clock=clock)
    source = BybitLiveSource(clock=clock)
    try:
        # F12 requires a fresh synchronized reference in this process epoch.
        clock.sample()
        clock.sample()
        instruments = await historical.discover("spot")
        instrument = next(
            (item for item in instruments if item.identity.symbol == _SYMBOL), None
        )
        if instrument is None:
            raise RuntimeError("Bybit spot metadata omitted the requested instrument")
        request = StreamRequest(
            BYBIT_CANDLE_SCHEMA, InstrumentId("bybit", "spot", _SYMBOL)
        )
        accepted = 0
        final_seen = False
        try:
            async with aclosing(source.stream(request)) as stream:
                async with asyncio.timeout(75):
                    async for envelope in stream:
                        ingestor.accept(envelope)
                        accepted += 1
                        final_seen = bool(json.loads(envelope.payload)["finalized"])
                        if final_seen or accepted >= 8:
                            break
        except TimeoutError:
            pass  # Keep every durably accepted open candle for the dataset check.
        if not accepted:
            raise RuntimeError("Bybit live stream produced no candle")
        return accepted, final_seen, instrument
    finally:
        await source.close()
        await historical.close()


def main(
    *,
    capture: Callable[
        [DurableIngestor, ClockMonitor],
        Coroutine[Any, Any, tuple[int, bool, Instrument]],
    ]
    | None = None,
    code_revision: str = "f14-public-live-check",
) -> None:
    with tempfile.TemporaryDirectory(prefix="scryntic-f14-") as temporary:
        installation = _installation(Path(temporary))
        limits = _publication_limits()
        clock = ClockMonitor(SystemClockReader(), ChronyStatus(), ClockLimits())
        with ExitStack() as stack:
            ingestor = DurableIngestor(
                installation,
                producer=_PRODUCER,
                epoch="public-live-check",
                capacity=128,
                max_payload_bytes=8192,
            )
            stack.callback(ingestor.close)
            normalization = NormalizationStore(installation, producer=_PRODUCER)
            stack.callback(normalization.close)
            publication = PublicationStore(installation, producer=_PRODUCER)
            stack.callback(publication.close)
            accepted, final_seen, instrument = asyncio.run(
                (capture or _capture)(ingestor, clock)
            )
            normalized = 0
            for _ in range(accepted + 1):
                outcome = process_next(
                    ingestor,
                    normalization,
                    {instrument.identity: instrument},
                    normalized_at_ns=time.time_ns(),
                )
                if isinstance(outcome, NoWork):
                    break
                if isinstance(outcome, Blocked):
                    raise RuntimeError("Bybit live normalization blocked")
                normalized += 1
            if normalized != accepted:
                raise RuntimeError("Bybit live normalization missed committed records")
            coordinator = PublicationCoordinator(
                ingestor,
                normalization,
                publication,
                ParquetRawArchive(installation),
                NormalizedParquetArchive(installation),
                ManifestStorage(installation),
                installation,
                limits,
            )
            published = 0
            for _ in range(accepted + 1):
                publish_outcome = coordinator.publish_next()
                if isinstance(publish_outcome, NoPublishableWork):
                    break
                if isinstance(publish_outcome, WaitingForNormalization):
                    raise RuntimeError("Bybit live publication awaits normalization")
                published += 1
            reader = PublicationReader(publication, installation, limits)
            hashes = _catalog_hashes(reader, limits)
            lock_hash = hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest()
            dataset = (
                DatasetBuilder(
                    reader,
                    installation,
                    code_revision=code_revision,
                    dependency_lock_sha256=lock_hash,
                )
                .build(BuildDatasetRequest(hashes, CANDLE_RECIPE_SCHEMA))
                .dataset
            )
            rows = DatasetReader(installation).read_table(dataset).to_pylist()
            print(
                json.dumps(
                    {
                        "source": "bybit-public",
                        "symbol": _SYMBOL,
                        "accepted": accepted,
                        "normalized": normalized,
                        "published": published,
                        "dataset_rows": len(rows),
                        "dataset_finalized_rows": sum(
                            bool(row["finalized"]) for row in rows
                        ),
                        "source_confirm_and_f12_final": final_seen,
                        "clock_status": clock.sample().quality.status,
                    },
                    sort_keys=True,
                )
            )
            if not rows:
                raise RuntimeError("Bybit live dataset contains no candle")


if __name__ == "__main__":
    main()
