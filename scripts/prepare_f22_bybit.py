"""Bounded public capture -> F15 coverage -> F16 import -> F18/F19 evidence."""

import argparse
import asyncio
import json
import shutil
import time
from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from scripts.check_bybit_live import _installation
from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.sources import BYBIT_CANDLE_SCHEMA, StreamRequest
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.clock.chrony import ChronyStatus
from scryntic.clock.host import SystemClockReader
from scryntic.clock.monitor import ClockMonitor
from scryntic.composition import _publication_limits
from scryntic.configuration.clock import ClockLimits
from scryntic.configuration.paths import Installation
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.domain.identity import InstrumentId
from scryntic.domain.market import Instrument
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportLimits, parse_request
from scryntic.ingestion.sqlite_spool import DurableIngestor
from scryntic.normalization.runner import Blocked, NoWork, process_next
from scryntic.normalization.sqlite_store import NormalizationStore
from scryntic.publication.coordinator import (
    NoPublishableWork,
    PublicationCoordinator,
    WaitingForNormalization,
)
from scryntic.publication.manifest import ManifestStorage
from scryntic.publication.sqlite_store import PublicationStore
from scryntic.recovery.budget import SharedBudget
from scryntic.recovery.coverage import CoverageLedger, Family, RecoveryStream
from scryntic.recovery.supervisor import RepairLimits, SourceSupervisor
from scryntic.replay.contracts import ReplayConfig
from scryntic.replay.service import ReplayService
from scryntic.sources.bybit import BybitHistoricalSource
from scryntic.sources.bybit_live import BybitLiveSource
from scryntic.sources.bybit_recovery import candle_evidence

STEP = 60_000_000_000


async def capture(
    spool: DurableIngestor, clock: ClockMonitor, start: int, end: int
) -> tuple[Instrument, tuple[CoverageClaim, ...]]:
    history, source = BybitHistoricalSource(clock=clock), BybitLiveSource(clock=clock)
    try:
        clock.sample()
        clock.sample()
        subject = InstrumentId("bybit", "spot", "BTCUSDT")
        instrument = next(
            item for item in await history.discover("spot") if item.identity == subject
        )
        ledger = CoverageLedger(
            spool,
            RecoveryStream(
                "f22-btc-1",
                "bybit-public",
                "market-kline",
                "kline-1",
                subject,
                Family.CANDLE,
                "backfill",
            ),
            baseline_ns=start,
        )
        ledger.begin(clock.sample())
        ledger.loss(start, end, "qualification-backfill", clock.sample())
        supervisor = SourceSupervisor(
            source=source,
            request=StreamRequest(BYBIT_CANDLE_SCHEMA, subject),
            spool=spool,
            ledger=ledger,
            budget=SharedBudget(records=64, bytes_=1024**2),
            clock=clock,
            history=history,
            candle_parser=candle_evidence,
            interval_ns=STEP,
            limits=RepairLimits(page_records=16),
        )
        # Actual F15 durable repair evidence, not an invented coverage claim.
        for _ in range(64):
            if not any(span.status == "pending" for span in ledger.snapshot.spans):
                break
            await supervisor.repair_once()
            await asyncio.sleep(1.05)
        ledger.finish()
        claims = tuple(
            CoverageClaim(
                subject, STEP, span.start_ns, span.end_ns, span.status, span.detected
            )
            for span in ledger.snapshot.spans
            if span.end_ns is not None
        )
        return instrument, claims
    finally:
        await source.close()
        await history.close()


def _import_publications(
    catalog: ImportCatalog,
    publication: PublicationStore,
    installation: Installation,
    root: Path,
    hashes: tuple[str, ...],
    clock: ClockMonitor,
) -> None:
    limits = _publication_limits()
    for index, checksum in enumerate(hashes):
        entry = publication.catalog_by_hash(checksum)
        if entry is None:
            raise RuntimeError("Published manifest disappeared")
        raw = ManifestStorage(installation).read_exact(
            entry.ref, limits.max_manifest_bytes
        )
        document = parse_request(raw, ImportLimits())
        incoming = root / f"incoming-{index}"
        incoming.mkdir(mode=0o700)
        for obj in document.body.objects:
            source = (
                installation.state_dir
                / "archive/objects/sha256"
                / obj.sha256[:2]
                / f"{obj.sha256}.parquet"
            )
            shutil.copyfile(source, incoming / obj.sha256)
            (incoming / obj.sha256).chmod(0o600)
        catalog.accept(raw, incoming, clock.sample())


def prepare(root: Path, revision: str) -> None:
    if not root.is_absolute():
        raise ValueError("Qualification root must be absolute")
    root.mkdir(mode=0o700)
    installation = _installation(root)
    clock = ClockMonitor(SystemClockReader(), ChronyStatus(), ClockLimits())
    end = time.time_ns() // STEP * STEP - STEP
    start = end - 192 * STEP
    lock = sha256(Path("uv.lock").read_bytes()).hexdigest()
    limits = _publication_limits()
    with ExitStack() as stack:
        spool = stack.enter_context(
            DurableIngestor(
                installation,
                producer="f22-bybit",
                epoch="public-history",
                capacity=512,
                max_payload_bytes=8192,
            )
        )
        instrument, coverage = asyncio.run(capture(spool, clock, start, end))
        normalization = NormalizationStore(installation, producer="f22-bybit")
        publication = PublicationStore(installation, producer="f22-bybit")
        stack.callback(normalization.close)
        stack.callback(publication.close)
        for _ in range(513):
            outcome = process_next(
                spool,
                normalization,
                {instrument.identity: instrument},
                normalized_at_ns=time.time_ns(),
            )
            if isinstance(outcome, NoWork):
                break
            if isinstance(outcome, Blocked):
                raise RuntimeError("Captured history normalization blocked")
        coordinator = PublicationCoordinator(
            spool,
            normalization,
            publication,
            ParquetRawArchive(installation),
            NormalizedParquetArchive(installation),
            ManifestStorage(installation),
            installation,
            limits,
        )
        hashes_list: list[str] = []
        for _ in range(513):
            published = coordinator.publish_next()
            if isinstance(published, NoPublishableWork):
                break
            if isinstance(published, WaitingForNormalization):
                raise RuntimeError("Publication awaits normalization")
            hashes_list.append(published.manifest.manifest_hash)
        hashes = tuple(hashes_list)
        if not hashes:
            raise RuntimeError("Bybit capture produced no publications")
        with ImportCatalog(installation) as catalog:
            _import_publications(
                catalog, publication, installation, root, hashes, clock
            )
            datasets = DatasetService(
                catalog,
                installation,
                code_revision=revision,
                dependency_lock_sha256=lock,
            )
            reference = datasets.build(
                BuildDatasetRequest(hashes, F18_RECIPE_SCHEMA),
                policy=RecipePolicy(
                    finalized_only=True,
                    label_horizon_steps=1,
                    coverage="exclude",
                    time_quality="exclude",
                ),
                coverage=coverage,
            ).dataset
            config = ReplayConfig(start, start + 96 * STEP, start + 144 * STEP, end)
            replay = ReplayService(
                datasets, code_revision=revision, dependency_lock_sha256=lock
            )
            report = replay.evaluate(reference, config)
            (root / "baseline.json").write_bytes(report.canonical_bytes)
            evidence: dict[str, Any] = {
                "source": "bybit-public",
                "symbol": "BTCUSDT",
                "requested_start_ns": start,
                "requested_end_ns": end,
                "accepted_records": spool.status().accepted_offset,
                "coverage": [item.projection() for item in coverage],
                "clock": asdict(clock.sample()),
                "dataset": asdict(reference),
                "replay": config.projection(),
                "baseline_sha256": report.sha256,
            }
            (root / "capture.json").write_bytes(canonical_json_bytes(evidence))
            print(
                json.dumps(
                    {
                        "dataset_manifest_sha256": reference.manifest_sha256,
                        "rows": reference.row_count,
                        "baseline_sha256": report.sha256,
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    prepare(args.root, args.code_revision)
