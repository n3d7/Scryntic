"""Bounded transport metadata and explicit local enrollment contracts."""

import base64
import ipaddress
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from scryntic.domain.validation import digest, identifier, integer
from scryntic.publication.manifest import GENESIS_MANIFEST_HASH, ManifestRef

_INVALID_HOST_KEY = "Invalid pinned host key"


class PullError(RuntimeError):
    """Only fixed local messages cross the transport boundary."""


def relative_path(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or value.startswith("/")
        or any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", p) is None
            for p in value.split("/")
        )
    ):
        raise ValueError("Invalid remote path")


@dataclass(frozen=True, slots=True)
class Enrollment:
    name: str
    host: str
    port: int
    username: str
    remote_root: str
    host_key: str
    producer: str
    epoch: str

    def __post_init__(self) -> None:
        for value in (self.name, self.username, self.producer, self.epoch):
            identifier(value)
        if type(self.host) is not str:
            raise ValueError("Invalid enrolled address")
        ipaddress.ip_address(self.host)
        integer(self.port, 1)
        if self.port > 65535:
            raise ValueError("Invalid SSH port")
        root = PurePosixPath(self.remote_root)
        if (
            not root.is_absolute()
            or str(root) != self.remote_root
            or self.remote_root == "/"
        ):
            raise ValueError("Invalid artifact root")
        relative_path(self.remote_root[1:])
        if type(self.host_key) is not str or len(self.host_key) > 128:
            raise ValueError(_INVALID_HOST_KEY)
        fields = self.host_key.split(" ")
        if len(fields) != 2 or fields[0] != "ssh-ed25519":
            raise ValueError("Expected explicit Ed25519 host key")
        try:
            blob = base64.b64decode(fields[1], validate=True)
        except ValueError:
            raise ValueError(_INVALID_HOST_KEY) from None
        if (
            len(blob) != 51
            or blob[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
            or base64.b64encode(blob).decode("ascii") != fields[1]
        ):
            raise ValueError(_INVALID_HOST_KEY)


@dataclass(frozen=True, slots=True)
class Anchor:
    producer: str
    epoch: str
    sequence: int
    manifest_hash: str

    def __post_init__(self) -> None:
        identifier(self.producer)
        identifier(self.epoch)
        integer(self.sequence, 0)
        digest(self.manifest_hash)
        if self.sequence > 2**63 - 1 or (
            (self.sequence == 0) != (self.manifest_hash == GENESIS_MANIFEST_HASH)
        ):
            raise ValueError("Invalid accepted anchor")

    @property
    def reference(self) -> ManifestRef | None:
        if self.sequence == 0:
            return None
        return ManifestRef(self.producer, self.epoch, self.sequence, self.manifest_hash)


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    name: str
    mode: int
    size: int

    def __post_init__(self) -> None:
        relative_path(self.name)
        if "/" in self.name:
            raise ValueError("Invalid directory entry")
        integer(self.mode, 0)
        integer(self.size, 0)


@dataclass(frozen=True, slots=True)
class PullLimits:
    chunk_bytes: int = 65536
    operation_seconds: int = 10
    wall_seconds: int = 120
    max_entries: int = 10000
    max_manifests: int = 10000
    max_new_manifests: int = 16
    max_discovery_bytes: int = 64 * 1024 * 1024
    max_transfer_bytes: int = 128 * 1024 * 1024
    max_staging_bytes: int = 128 * 1024 * 1024
    max_backup_bytes: int = 128 * 1024 * 1024

    def __post_init__(self) -> None:
        ceilings = (
            65536,
            60,
            600,
            100000,
            100000,
            128,
            256 * 1024**2,
            1024**3,
            1024**3,
            256 * 1024**2,
        )
        values = (
            self.chunk_bytes,
            self.operation_seconds,
            self.wall_seconds,
            self.max_entries,
            self.max_manifests,
            self.max_new_manifests,
            self.max_discovery_bytes,
            self.max_transfer_bytes,
            self.max_staging_bytes,
            self.max_backup_bytes,
        )
        for value, maximum in zip(values, ceilings, strict=True):
            integer(value, 1)
            if value > maximum:
                raise ValueError("Pull limit exceeds ceiling")


class ReadOnlyRemote(Protocol):
    @property
    def enrollment(self) -> Enrollment: ...

    async def entries(self, relative: str) -> tuple[RemoteEntry, ...]: ...

    async def read(self, relative: str, offset: int, count: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PullResult:
    anchor: Anchor
    imported: tuple[ManifestRef, ...]
    transferred_bytes: int
    transferred_objects: int
    complete: bool
    freshness: str
