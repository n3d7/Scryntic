"""Bounded append-only audit artifact, independent of SQLite connection owners."""

import fcntl
import json
import os
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import TracebackType
from typing import Any, cast

from scryntic.archive.canonical import JsonObject, canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest, identifier, integer
from scryntic.sqlite_state import open_private_file

_JOURNAL = "research-ledger.jsonl"
_ANCHOR = "research-ledger.anchor"
_ZERO = "0" * 64
_SCHEMA: JsonObject = {"name": "scryntic.research-ledger", "major": 1, "minor": 0}
_MAX_BYTES = 8 * 1024 * 1024
_MAX_EVENTS = 10000


class ResearchDenied(ValueError):
    """Reserved/unknown data cannot enter development workflows."""


@dataclass(frozen=True, slots=True)
class Reservation:
    policy_sha256: str
    final_start_ns: int
    final_end_ns: int
    development_end_ns: int

    def __post_init__(self) -> None:
        digest(self.policy_sha256)
        for value in (
            self.final_start_ns,
            self.final_end_ns,
            self.development_end_ns,
        ):
            integer(value, 1)
        if not self.development_end_ns <= self.final_start_ns < self.final_end_ns:
            raise ValueError("Invalid chronological reservation")


class ResearchLedger:
    """Trusted application owner only; workers never receive these descriptors.

    Hashes detect corruption; the separately durable head detects committed-prefix
    loss. This is not protection against an administrator rewriting both files.
    """

    def __init__(
        self, installation: Installation, *, clock: Callable[[], int] = time.time_ns
    ):
        self._resources = ExitStack()
        self._clock = clock
        self._closed = False
        self._failed = False
        self.events: list[dict[str, Any]] = []
        try:
            self._directory = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            self._uid = installation.owner_uid
            self._fd = open_private_file(self._directory, _JOURNAL, self._uid)
            self._resources.callback(os.close, self._fd)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def active(installation: Installation) -> bool:
        return any(
            os.path.lexists(installation.state_dir / name)
            for name in (_JOURNAL, _ANCHOR)
        )

    def __enter__(self) -> "ResearchLedger":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        self._resources.close()

    def _check(self) -> None:
        if self._closed or self._failed:
            raise ValueError("Research ledger unavailable")
        pinned = os.fstat(self._fd)
        current = os.stat(_JOURNAL, dir_fd=self._directory, follow_symlinks=False)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Research ledger identity changed")

    @property
    def reservation(self) -> Reservation:
        if not self.events or self.events[0]["kind"] != "reservation":
            raise ResearchDenied("Research reservation missing")
        return Reservation(**self.events[0]["body"])

    def _load(self) -> None:
        self._check()
        if os.fstat(self._fd).st_size > _MAX_BYTES:
            raise ValueError("Research ledger quota exceeded")
        data = os.pread(self._fd, _MAX_BYTES + 1, 0)
        if data and not data.endswith(b"\n"):
            raise ValueError("Incomplete research ledger tail")
        self._load_events(data)
        if len(self.events) > _MAX_EVENTS:
            raise ValueError("Research ledger event quota exceeded")
        self._load_anchor(bool(data))

    def _load_events(self, data: bytes) -> None:
        previous = _ZERO
        for line in data.splitlines():
            event = json.loads(line)
            if type(event) is not dict or set(event) != {
                "schema",
                "sequence",
                "previous",
                "timestamp_ns",
                "kind",
                "subject",
                "body",
            }:
                raise ValueError("Invalid research event")
            if (
                event["schema"] != _SCHEMA
                or type(event["schema"]["major"]) is not int
                or type(event["schema"]["minor"]) is not int
            ):
                raise ValueError("Unsupported research event schema")
            integer(event["sequence"], 1)
            integer(event["timestamp_ns"], 0)
            identifier(event["subject"])
            if (
                event["sequence"] != len(self.events) + 1
                or event["previous"] != previous
                or canonical_json_bytes(event) != line
                or (
                    self.events
                    and event["timestamp_ns"] < self.events[-1]["timestamp_ns"]
                )
            ):
                raise ValueError("Invalid research event chain")
            self._validate_body(event["kind"], event["body"])
            if event["kind"] == "reservation" and self.events:
                raise ValueError("Research reservation cannot be replaced")
            previous = sha256(line).hexdigest()
            self.events.append(event)

    def _load_anchor(self, has_events: bool) -> None:
        try:
            anchor = self._read_anchor()
        except FileNotFoundError:
            if has_events:
                raise ValueError("Research anchor missing") from None
            self._anchor()
            return
        n = anchor["sequence"]
        if n > len(self.events) or anchor["head"] != (
            _ZERO if n == 0 else self._event_hash(self.events[n - 1])
        ):
            raise ValueError("Committed research history lost")
        if n < len(self.events):
            self._anchor()  # complete fsynced append survived before head update

    def _read_anchor(self) -> dict[str, Any]:
        fd = os.open(
            _ANCHOR,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=self._directory,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self._uid
                or info.st_mode & 0o777 != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError("Unsafe research anchor")
            raw = os.read(fd, 1025)
        finally:
            os.close(fd)
        anchor = json.loads(raw)
        if (
            type(anchor) is not dict
            or set(anchor) != {"schema", "sequence", "head"}
            or anchor["schema"] != _SCHEMA
            or type(anchor["schema"]["major"]) is not int
            or type(anchor["schema"]["minor"]) is not int
            or canonical_json_bytes(anchor) != raw
        ):
            raise ValueError("Unsupported research anchor")
        integer(anchor["sequence"], 0)
        digest(anchor["head"])
        return anchor

    @staticmethod
    def _event_hash(event: dict[str, Any]) -> str:
        return sha256(canonical_json_bytes(event)).hexdigest()

    @staticmethod
    def _validate_body(kind: str, body: object) -> None:
        if type(body) is not dict:
            raise ValueError("Invalid research event body")
        fields = cast("dict[str, Any]", body)
        if kind == "reservation":
            Reservation(**fields)
        elif kind in ("access_allowed", "access_denied"):
            if set(fields) != {"operation", "manifest_sha256"} or fields[
                "operation"
            ] not in ("inspect", "export", "build", "pi02-build"):
                raise ValueError("Invalid access event")
            digest(fields["manifest_sha256"])
        elif kind == "trial":
            if set(fields) != {"config_sha256", "phase", "attempt"} or fields[
                "phase"
            ] not in (
                "registered",
                "started",
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "deadline_expired",
                "intervention",
            ):
                raise ValueError("Invalid trial event")
            digest(fields["config_sha256"])
            integer(fields["attempt"], 0)
            if fields["attempt"] > 8:
                raise ValueError("Trial attempt quota exceeded")
        else:
            raise ValueError("Unknown research event kind")

    def _anchor(self) -> None:
        raw = canonical_json_bytes(
            {
                "schema": _SCHEMA,
                "sequence": len(self.events),
                "head": _ZERO if not self.events else self._event_hash(self.events[-1]),
            }
        )
        name = ".research-head-" + uuid.uuid4().hex
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._directory,
        )
        try:
            self._write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(
            name, _ANCHOR, src_dir_fd=self._directory, dst_dir_fd=self._directory
        )
        os.fsync(self._directory)

    @staticmethod
    def _write(fd: int, data: bytes) -> None:
        while data:
            n = os.write(fd, data)
            if n <= 0:
                raise OSError("Research append incomplete")
            data = data[n:]

    def _append(self, kind: str, subject: str, body: dict[str, Any]) -> None:
        self._check()
        identifier(subject)
        self._validate_body(kind, body)
        now = self._clock()
        integer(now, 0)
        if self.events and now < self.events[-1]["timestamp_ns"]:
            raise ValueError("Research clock regressed")
        event: dict[str, Any] = {
            "schema": _SCHEMA,
            "sequence": len(self.events) + 1,
            "previous": _ZERO if not self.events else self._event_hash(self.events[-1]),
            "timestamp_ns": now,
            "kind": kind,
            "subject": subject,
            "body": body,
        }
        raw = canonical_json_bytes(event) + b"\n"
        if (
            len(self.events) >= _MAX_EVENTS
            or os.fstat(self._fd).st_size + len(raw) > _MAX_BYTES
        ):
            raise ValueError("Research ledger quota exceeded")
        try:
            os.lseek(self._fd, 0, os.SEEK_END)
            self._write(self._fd, raw)
            os.fsync(self._fd)
            self.events.append(event)
            self._anchor()
        except BaseException:
            self._failed = True
            raise

    def reserve(self, reservation: Reservation) -> None:
        if self.events:
            if self.reservation != reservation:
                raise ValueError("Research reservation cannot be replaced")
            return
        if reservation.final_start_ns <= self._clock():
            raise ValueError("Final reservation must be genuinely future")
        self._append("reservation", reservation.policy_sha256, asdict(reservation))

    def check_dataset(
        self, manifest_sha256: str, manifest: dict[str, Any], operation: str
    ) -> None:
        r = self.reservation
        times = manifest.get("time_range_ns")
        allowed = (
            operation != "build"
            and type(times) is list
            and len(times) == 2
            and all(type(t) is int and 0 <= t < r.development_end_ns for t in times)
            and times[0] <= times[1]
            and not manifest.get("policy", {}).get("label_horizon_steps", 0)
        )
        self._append(
            "access_allowed" if allowed else "access_denied",
            manifest_sha256,
            {"operation": operation, "manifest_sha256": manifest_sha256},
        )
        if not allowed:
            raise ResearchDenied("Reserved, labelled or unqualified range denied")

    def check_pi02_input(self, manifest_sha256: str, manifest: dict[str, Any]) -> None:
        """Admit one verified development-only v2 input without opening v1 builds."""
        r = self.reservation
        fields = {
            "schema",
            "policy_sha256",
            "source_id",
            "snapshot_sha256",
            "source_use_sha256",
            "instrument",
            "availability_plane",
            "time_range_ns",
            "row_count",
            "label_horizon_seconds",
        }
        valid_shape = type(manifest) is dict and set(manifest) == fields
        times = manifest.get("time_range_ns") if valid_shape else None
        try:
            allowed = bool(
                valid_shape
                and manifest_sha256
                == sha256(canonical_json_bytes(manifest)).hexdigest()
                and manifest["schema"] == "scryntic.pi02.development-input.v1"
                and manifest["policy_sha256"] == r.policy_sha256
                and manifest["source_id"] == "binance-vision-usdm-1h"
                and manifest["instrument"] in ("BTCUSDT", "ETHUSDT")
                and manifest["availability_plane"] == "historical-reconstruction"
                and type(manifest["snapshot_sha256"]) is str
                and len(manifest["snapshot_sha256"]) == 64
                and type(manifest["source_use_sha256"]) is str
                and len(manifest["source_use_sha256"]) == 64
                and type(manifest["row_count"]) is int
                and 0 < manifest["row_count"] <= 120_000
                and type(manifest["label_horizon_seconds"]) is int
                and manifest["label_horizon_seconds"] == 0
                and type(times) is list
                and len(times) == 2
                and all(
                    type(t) is int and 0 <= t <= r.development_end_ns for t in times
                )
                and times[0] < times[1]
            )
            if allowed:
                digest(manifest["snapshot_sha256"])
                digest(manifest["source_use_sha256"])
        except (TypeError, ValueError):
            allowed = False
        self._append(
            "access_allowed" if allowed else "access_denied",
            manifest_sha256,
            {"operation": "pi02-build", "manifest_sha256": manifest_sha256},
        )
        if not allowed:
            raise ResearchDenied("PI02 input is not a qualified development snapshot")

    def trial(
        self, trial_id: str, config_sha256: str, phase: str, attempt: int
    ) -> None:
        _ = self.reservation
        body = {"config_sha256": config_sha256, "phase": phase, "attempt": attempt}
        self._validate_body("trial", body)
        same = [
            e for e in self.events if e["kind"] == "trial" and e["subject"] == trial_id
        ]
        if any(e["body"]["config_sha256"] != config_sha256 for e in same):
            raise ValueError("Trial identity conflict")
        if any(e["body"] == body for e in same):
            return
        if phase != "registered" and not same:
            raise ValueError("Trial must be registered before execution")
        if phase in ("registered", "started"):
            n = sum(
                e["kind"] == "trial" and e["body"]["phase"] == phase
                for e in self.events
            )
            if n >= 8:
                raise ValueError("Registered trial budget exhausted")
        self._append("trial", trial_id, body)


def guard_dataset(
    installation: Installation,
    manifest_sha256: str,
    manifest: dict[str, Any],
    operation: str,
) -> None:
    if ResearchLedger.active(installation):
        with ResearchLedger(installation) as ledger:
            ledger.check_dataset(manifest_sha256, manifest, operation)
