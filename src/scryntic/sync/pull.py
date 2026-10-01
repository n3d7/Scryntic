"""Bounded committed-chain discovery and opaque resumable object pulls."""

import asyncio
import json
import os
import re
import stat
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from hashlib import sha256

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.archive.model import ArchiveObject
from scryntic.domain.time import ClockSample
from scryntic.imports.filesystem import snapshot_file
from scryntic.imports.protocol import parse_request
from scryntic.publication.manifest import ManifestDocument, ManifestRef, parse_manifest
from scryntic.sync.catalog import PullCatalog
from scryntic.sync.model import (
    PullError,
    PullLimits,
    PullResult,
    ReadOnlyRemote,
    RemoteEntry,
)
from scryntic.sync.staging import Staging


@dataclass(frozen=True, slots=True)
class _Committed:
    document: ManifestDocument
    data: bytes


async def _bounded[T](operation: Awaitable[T], seconds: int) -> T:
    async with asyncio.timeout(seconds):
        return await operation


def _epoch_directory(producer: str, epoch: str) -> str:
    return sha256(
        canonical_json_bytes({"epoch": epoch, "producer": producer})
    ).hexdigest()


class _Discovery:
    def __init__(
        self, catalog: PullCatalog, remote: ReadOnlyRemote, limits: PullLimits
    ) -> None:
        self.catalog = catalog
        self.remote = remote
        self.limits = limits
        self.entries_seen = 0
        self.bytes_seen = 0

    async def entries(self, path: str) -> tuple[RemoteEntry, ...]:
        entries = await _bounded(
            self.remote.entries(path), self.limits.operation_seconds
        )
        if type(entries) is not tuple:
            raise PullError("Invalid directory response")
        self.entries_seen += len(entries)
        if self.entries_seen > self.limits.max_entries:
            raise PullError("Discovery entry quota exceeded")
        names: set[str] = set()
        for entry in entries:
            if type(entry) is not RemoteEntry or entry.name in names:
                raise PullError("Invalid directory response")
            RemoteEntry(entry.name, entry.mode, entry.size)
            names.add(entry.name)
        return entries

    async def read(self, path: str, offset: int, count: int) -> bytes:
        data = await _bounded(
            self.remote.read(path, offset, count), self.limits.operation_seconds
        )
        if type(data) is not bytes or len(data) > count:
            raise PullError("Invalid bounded transfer response")
        self.bytes_seen += len(data)
        if self.bytes_seen > self.limits.max_discovery_bytes:
            raise PullError("Discovery byte quota exceeded")
        return data

    async def manifest(self, path: str, entry: RemoteEntry) -> bytes:
        if (
            not stat.S_ISREG(entry.mode)
            or not 0 < entry.size <= self.catalog.limits.max_manifest_bytes
        ):
            raise PullError("Invalid committed manifest")
        data = bytearray()
        while len(data) <= entry.size:
            block = await self.read(
                path,
                len(data),
                min(self.limits.chunk_bytes, entry.size + 1 - len(data)),
            )
            if not block:
                break
            data.extend(block)
        if len(data) != entry.size:
            raise PullError("Committed manifest length mismatch")
        return bytes(data)

    async def _slot(self, epoch: str, slot: RemoteEntry) -> _Committed | None:
        if (
            not stat.S_ISDIR(slot.mode)
            or re.fullmatch(r"[0-9]{20}", slot.name) is None
            or not 0 < int(slot.name) <= 2**63 - 1
        ):
            raise PullError("Invalid committed sequence namespace")
        path = "manifests/" + epoch + "/" + slot.name
        entries = await self.entries(path)
        if not entries:
            return None
        if (
            len(entries) != 1
            or re.fullmatch(r"[0-9a-f]{64}\.json", entries[0].name) is None
        ):
            raise PullError("Conflicting committed sequence")
        entry = entries[0]
        data = await self.manifest(path + "/" + entry.name, entry)
        document = parse_manifest(data, self.catalog.limits.max_manifest_bytes)
        ref = document.ref
        if (
            ref.sequence != int(slot.name)
            or entry.name != ref.manifest_hash + ".json"
            or _epoch_directory(ref.producer, ref.epoch) != epoch
        ):
            raise PullError("Committed namespace identity mismatch")
        return _Committed(document, data)

    async def scan(self) -> dict[tuple[str, int], _Committed]:
        result: dict[tuple[str, int], _Committed] = {}
        for epoch in await self.entries("manifests"):
            if (
                not stat.S_ISDIR(epoch.mode)
                or re.fullmatch(r"[0-9a-f]{64}", epoch.name) is None
            ):
                raise PullError("Invalid committed epoch namespace")
            base = "manifests/" + epoch.name
            for slot in await self.entries(base):
                item = await self._slot(epoch.name, slot)
                if item is None:
                    continue
                ref = item.document.ref
                if ref.producer != self.remote.enrollment.producer:
                    continue
                parse_request(item.data, self.catalog.limits)
                if ref.epoch not in self.catalog.epochs(self.remote.enrollment.name):
                    raise PullError("Unexpected producer epoch")
                key = ref.epoch, ref.sequence
                if key in result or len(result) >= self.limits.max_manifests:
                    raise PullError("Committed history quota or identity conflict")
                result[key] = item
        return result

    async def hint(self) -> tuple[ManifestRef | None, bool]:
        entries = await self.entries(".")
        head = next((entry for entry in entries if entry.name == "head.json"), None)
        if head is None:
            return None, False
        if not stat.S_ISREG(head.mode) or not 0 < head.size <= 1024:
            return None, True
        data = await self.manifest("head.json", head)
        try:
            value = json.loads(data)
            if (
                type(value) is not dict
                or set(value) != {"producer", "epoch", "sequence", "manifest_hash"}
                or canonical_json_bytes(value) != data
            ):
                return None, True
            return ManifestRef(**value), True
        except Exception:
            return None, True


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise PullError("Pull deadline exceeded")


def _successor(
    document: ManifestDocument,
    sequence: int,
    previous_hash: str,
    prior: ManifestDocument | None,
    genesis: bool,
) -> None:
    body = document.body
    if body.sequence != sequence or body.previous_manifest_hash != previous_hash:
        raise PullError("Missing or conflicting chain predecessor")
    if (
        prior is not None and body.checkpoint_before != prior.body.checkpoint_after
    ) or (prior is None and genesis and body.checkpoint_before is not None):
        raise PullError("Publication checkpoint continuity mismatch")


def _epoch_history(
    catalog: PullCatalog,
    name: str,
    epoch: str,
    committed: dict[tuple[str, int], _Committed],
    previous_epoch: ManifestDocument | None,
    deadline: float,
) -> tuple[list[_Committed], ManifestDocument | None]:
    _check_deadline(deadline)
    bootstrap = catalog.bootstrap(name, epoch)
    anchor = catalog.anchor(name, epoch)
    previous = (
        committed.get((epoch, bootstrap.sequence)) if bootstrap.sequence else None
    )
    previous_hash = bootstrap.manifest_hash
    selected = sorted(
        (
            item
            for (e, s), item in committed.items()
            if e == epoch and s > bootstrap.sequence
        ),
        key=lambda item: item.document.body.sequence,
    )
    expected = bootstrap.sequence + 1
    candidates: list[_Committed] = []
    for item in selected:
        _check_deadline(deadline)
        body = item.document.body
        prior = previous.document if previous is not None else previous_epoch
        _successor(
            item.document, expected, previous_hash, prior, bootstrap.sequence == 0
        )
        if epoch != catalog.enrollment(name).epoch and body.sequence > anchor.sequence:
            raise PullError("Retired epoch acquired unexpected history")
        if body.sequence > anchor.sequence or not catalog.is_imported(
            item.document.ref
        ):
            candidates.append(item)
        previous, previous_hash = item, item.document.manifest_hash
        expected += 1
    if expected - 1 < anchor.sequence:
        raise PullError("Accepted history was truncated")
    return candidates, previous.document if previous is not None else previous_epoch


def _validate_history(
    catalog: PullCatalog,
    name: str,
    committed: dict[tuple[str, int], _Committed],
    deadline: float,
) -> tuple[_Committed, ...]:
    retained = catalog.history(name)
    for data in retained:
        _check_deadline(deadline)
        document = parse_request(data, catalog.limits)
        remote = committed.get((document.body.epoch, document.body.sequence))
        if remote is None or remote.data != data:
            raise PullError("Accepted history was rewritten or truncated")
    candidates: list[_Committed] = []
    previous_epoch: ManifestDocument | None = None
    for epoch in catalog.epochs(name):
        entries, previous_epoch = _epoch_history(
            catalog, name, epoch, committed, previous_epoch, deadline
        )
        candidates.extend(entries)
    return tuple(candidates)


def _cached(catalog: PullCatalog, stage: Staging, item: ArchiveObject) -> bool:
    try:
        os.stat(item.sha256, dir_fd=catalog._objects, follow_symlinks=False)
    except FileNotFoundError:
        return False
    snapshot = snapshot_file(
        catalog._objects,
        item.sha256,
        item.encoded_bytes,
        catalog.limits.max_encoded_bytes,
    )
    try:
        stage.cache(snapshot, item.sha256, item.encoded_bytes)
    finally:
        os.close(snapshot)
    return True


async def _download(
    remote: ReadOnlyRemote,
    stage: Staging,
    item: ArchiveObject,
    limits: PullLimits,
    remaining: int,
) -> int:
    transferred = 0
    path = f"objects/sha256/{item.sha256[:2]}/{item.sha256}.parquet"
    with stage.partial(item.sha256, item.encoded_bytes) as fd:
        offset = os.fstat(fd).st_size
        while offset < item.encoded_bytes:
            count = min(limits.chunk_bytes, item.encoded_bytes - offset)
            if transferred + count > remaining:
                raise PullError("Transfer byte quota exceeded")
            data = await _bounded(
                remote.read(path, offset, count), limits.operation_seconds
            )
            if type(data) is not bytes or not data or len(data) > count:
                raise PullError("Invalid object transfer response")
            stage.append(fd, data)
            offset += len(data)
            transferred += len(data)
        extra = await _bounded(remote.read(path, offset, 1), limits.operation_seconds)
        if extra != b"":
            raise PullError("Object length mismatch")
        stage.finish(fd, item.sha256, item.encoded_bytes)
    return transferred


def _freshness(
    hint: ManifestRef | None, present: bool, current: ManifestRef | None
) -> str:
    if not present:
        return "unknown"
    if hint is not None and hint == current:
        return "current"
    return "degraded"


async def _import_pending(
    catalog: PullCatalog,
    name: str,
    remote: ReadOnlyRemote,
    receipt: ClockSample,
    bounds: PullLimits,
    pending: tuple[_Committed, ...],
    deadline: float,
) -> tuple[list[ManifestRef], int, int]:
    imported: list[ManifestRef] = []
    transferred = objects = 0
    with Staging(catalog._installation, bounds) as stage:
        for entry in pending[: bounds.max_new_manifests]:
            for item in entry.document.body.objects:
                _check_deadline(deadline)
                if stage.has(item.sha256, item.encoded_bytes) or _cached(
                    catalog, stage, item
                ):
                    continue
                transferred += await _download(
                    remote, stage, item, bounds, bounds.max_transfer_bytes - transferred
                )
                objects += 1
            imported.append(
                catalog.accept_next(
                    name, entry.data, stage.path, receipt, deadline=deadline
                )
            )
            for item in entry.document.body.objects:
                stage.discard(item.sha256)
        _check_deadline(deadline)
    return imported, transferred, objects


async def pull(
    catalog: PullCatalog,
    name: str,
    remote: ReadOnlyRemote,
    receipt: ClockSample,
    limits: PullLimits | None = None,
) -> PullResult:
    bounds = catalog.pull_limits if limits is None else limits
    try:
        endpoint = catalog.enrollment(name)
        if remote.enrollment != endpoint or not isinstance(receipt, ClockSample):
            raise PullError("Pull enrollment mismatch")
        deadline = time.monotonic() + bounds.wall_seconds

        async with asyncio.timeout(bounds.wall_seconds):
            discovery = _Discovery(catalog, remote, bounds)
            committed = await discovery.scan()
            pending = _validate_history(catalog, name, committed, deadline)
            hint, present = await discovery.hint()
            current = max(
                (
                    item.document.ref
                    for (epoch, _), item in committed.items()
                    if epoch == endpoint.epoch
                ),
                key=lambda ref: ref.sequence,
                default=None,
            )
            freshness = _freshness(hint, present, current)
            if freshness == "degraded":
                committed = await discovery.scan()
                pending = _validate_history(catalog, name, committed, deadline)
            imported, transferred, objects = await _import_pending(
                catalog, name, remote, receipt, bounds, pending, deadline
            )
            return PullResult(
                catalog.anchor(name),
                tuple(imported),
                transferred,
                objects,
                len(pending) <= bounds.max_new_manifests,
                freshness,
            )
    except Exception:
        raise PullError("Pull failed; accepted anchors were not reset") from None
