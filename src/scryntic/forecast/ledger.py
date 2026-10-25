"""Single-owner append-only publication journal and post-COMMIT certificates."""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from contextlib import ExitStack
from dataclasses import asdict
from types import TracebackType
from typing import Any, cast
from uuid import uuid4

from scryntic.application.clock import Clock
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.configuration.paths import Installation, directory
from scryntic.domain.validation import digest, identifier
from scryntic.forecast.ledger_contracts import (
    FORWARD_SOURCE,
    ForecastContent,
    ForwardAdmission,
    decoded,
    identity,
    timeliness,
)
from scryntic.sqlite_state import connect_pinned, open_private_file, schema_matches

_CREATE = "CREATE TABLE events (sequence INTEGER PRIMARY KEY NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL)"
_SCHEMA = (("table", "events", "events", _CREATE),)
_KINDS = {
    "publication",
    "receipt",
    "conflict",
    "slot",
    "inference",
    "outcome",
    "score",
    "input",
    "input-observation",
    "input-receipt",
    "stream",
    "maturation",
}
_DB = "forecast-ledger.sqlite3"


class ForecastLedger:
    def __init__(
        self,
        installation: Installation,
        clock: Clock,
        *,
        admission: ForwardAdmission | None = None,
    ) -> None:
        self.installation, self.clock = installation, clock
        self.admission = admission
        if admission is not None:
            admission.__post_init__()
        self._owner = threading.get_ident()
        self._resources = ExitStack()
        self._closed = False
        self._failed = False
        self._events: list[dict[str, Any]] = []
        self._bytes = 0
        try:
            self._dir = self._resources.enter_context(
                directory(installation.state_dir, installation.owner_uid, private=True)
            )
            lock = open_private_file(
                self._dir, "forecast-ledger.lock", installation.owner_uid
            )
            self._resources.callback(os.close, lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = open_private_file(self._dir, _DB, installation.owner_uid)
            self._resources.callback(os.close, self._fd)
            self._db = connect_pinned(
                self._dir,
                _DB,
                (("forecast-ledger.lock", lock), (_DB, self._fd)),
                self._resources,
            )
            if self._db.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise ValueError("PI04 WAL unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA max_page_count=4096")
            if self._db.execute("PRAGMA synchronous").fetchone() != (2,):
                raise ValueError("PI04 durability unavailable")
            if self._db.execute("PRAGMA user_version").fetchone() == (0,):
                if self._db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
                    raise ValueError("Invalid PI04 schema")
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(_CREATE)
                self._db.execute("PRAGMA user_version=1")
                self._db.execute("COMMIT")
                os.fsync(self._dir)
            if self._db.execute("PRAGMA user_version").fetchone() != (
                1,
            ) or not schema_matches(self._db, _SCHEMA):
                raise ValueError("Invalid PI04 schema")
            if self._db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Invalid PI04 journal")
            self._load()
            self._recover()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> ForecastLedger:
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
        if self._closed or self._failed or self._owner != threading.get_ident():
            raise ValueError("PI04 ledger unavailable")
        pinned = os.fstat(self._fd)
        current = os.stat(_DB, dir_fd=self._dir, follow_symlinks=False)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("PI04 journal identity changed")

    def _load(self) -> None:
        self._check()
        result: list[dict[str, Any]] = []
        previous, total = "0" * 64, 0
        for seq, payload, checksum in self._db.execute(
            "SELECT sequence,payload,sha256 FROM events ORDER BY sequence LIMIT 10001"
        ):
            if type(payload) is not bytes:
                raise ValueError("Invalid PI04 event bytes")
            total += len(payload)
            if total > 8 * 1024**2 or len(result) >= 10000:
                raise ValueError("PI04 journal quota exceeded")
            event = decoded(payload)
            if (
                set(event) != {"sequence", "previous", "kind", "subject", "body"}
                or seq != len(result) + 1
                or event["sequence"] != seq
                or event["previous"] != previous
                or identity(event) != checksum
                or event["kind"] not in _KINDS
                or type(event["body"]) is not dict
            ):
                raise ValueError("Invalid PI04 event chain")
            digest(event["subject"])
            if event["kind"] == "publication":
                content = ForecastContent.from_body(event["body"]["content"])
                if (
                    content.slot.sha256 != event["subject"]
                    or content.sha256 != event["body"]["content_sha256"]
                ):
                    raise ValueError("PI04 publication identity mismatch")
            result.append(event)
            previous = checksum
        self._events, self._bytes = result, total
        self._check_anchor()

    def _check_anchor(self) -> None:
        try:
            fd = os.open(
                "forecast-ledger.anchor",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=self._dir,
            )
        except FileNotFoundError:
            if self._events:
                raise ValueError("PI04 retained head missing") from None
            self._write_anchor()
            return
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.installation.owner_uid
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= 256
            ):
                raise ValueError("Unsafe PI04 retained head")
            head = decoded(os.read(fd, 257))
            sequence = head.get("sequence")
            if (
                set(head) != {"sequence", "sha256"}
                or type(sequence) is not int
                or not 0 <= sequence <= len(self._events)
            ):
                raise ValueError("PI04 committed history lost")
            expected = identity(self._events[sequence - 1]) if sequence else "0" * 64
            if head["sha256"] != expected:
                raise ValueError("PI04 retained history mismatch")
        finally:
            os.close(fd)

    def _write_anchor(self) -> None:
        temporary = "forecast-head-" + uuid4().hex
        data = canonical_json_bytes(
            {
                "sequence": len(self._events),
                "sha256": identity(self._events[-1]) if self._events else "0" * 64,
            }
        )
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._dir,
        )
        try:
            if os.write(fd, data) != len(data):
                raise OSError("Incomplete PI04 retained head")
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(
                temporary,
                "forecast-ledger.anchor",
                src_dir_fd=self._dir,
                dst_dir_fd=self._dir,
            )
            os.fsync(self._dir)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._dir)
            except FileNotFoundError:
                pass

    def events(self, kind: str) -> tuple[dict[str, Any], ...]:
        self._load()
        return tuple(
            decoded(canonical_json_bytes(event))
            for event in self._events
            if event["kind"] == kind
        )

    def _append(self, kind: str, subject: str, body: dict[str, Any]) -> str:
        self._load()
        if kind not in _KINDS or len(self._events) >= 10000:
            raise ValueError("PI04 event type or quota")
        digest(subject)
        event: dict[str, Any] = {
            "sequence": len(self._events) + 1,
            "previous": identity(self._events[-1]) if self._events else "0" * 64,
            "kind": kind,
            "subject": subject,
            "body": body,
        }
        data = canonical_json_bytes(event)
        decoded(data)
        if self._bytes + len(data) > 8 * 1024**2:
            raise ValueError("PI04 byte quota")
        checksum = identity(event)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "INSERT INTO events VALUES(?,?,?)", (event["sequence"], data, checksum)
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._failed = True
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        self._events.append(event)
        self._bytes += len(data)
        try:
            self._write_anchor()
        except BaseException:
            self._failed = True
            raise
        return checksum

    def _recover(self) -> None:
        receipts = {e["subject"] for e in self._events if e["kind"] == "receipt"}
        for event in tuple(self._events):
            if event["kind"] == "publication" and event["subject"] not in receipts:
                content = ForecastContent.from_body(event["body"]["content"])
                self._append(
                    "receipt",
                    event["subject"],
                    self._receipt_body(
                        content, "recovery-unknown", asdict(self.clock.sample())
                    ),
                )

    def _receipt_body(
        self, content: ForecastContent, timing: str, observation: dict[str, Any] | None
    ) -> dict[str, Any]:
        return {
            "slot_sha256": content.slot.sha256,
            "content_sha256": content.sha256,
            "disposition": "accepted",
            "timeliness": timing,
            "evidence_class": self._forward_class(content, timing)
            if content.slot.stream.evidence_class == "forward-shadow"
            else content.slot.stream.evidence_class,
            "deadline_ns": content.slot.deadline_ns,
            "post_commit_observation": observation,
        }

    def publication(self, slot_sha256: str) -> dict[str, Any]:
        digest(slot_sha256)
        found = [
            e["body"] for e in self.events("publication") if e["subject"] == slot_sha256
        ]
        if len(found) != 1:
            raise ValueError("PI04 publication not found or duplicated")
        return cast(dict[str, Any], found[0])

    def _forward_class(self, content: ForecastContent, timing: str) -> str:
        if timing == "late":
            return "late-forward"
        inputs = [
            e["body"]
            for e in self.events("input")
            if e["subject"] == content.input_sha256
        ]
        receipts = [
            e["body"]
            for e in self.events("input-receipt")
            if e["subject"] == content.input_sha256
            and e["body"]["latest_available_ns"] == content.latest_available_ns
            and e["body"]["timeliness"] == "on-time"
        ]
        if (
            self.admission is None
            or len(inputs) != 1
            or len(receipts) != 1
            or timing != "on-time"
        ):
            return "forward-unclassified"
        value, receipt = inputs[0], receipts[0]
        if (
            receipt["timeliness"] != "on-time"
            or receipt["latest_available_ns"] != content.latest_available_ns
            or value["origin_ns"] != content.slot.origin_ns
            or value["instrument"] != content.slot.instrument
            or value["reference_sha256"] != content.reference_sha256
            or value["close"] != str(content.reference_close)
        ):
            return "forward-unclassified"
        from scryntic.forecast.live import SourceRawStore

        SourceRawStore(self.installation).read(value["raw_sha256"])
        return "genuine-forward"

    def receipt(self, slot_sha256: str) -> dict[str, Any]:
        found = [
            e["body"] for e in self.events("receipt") if e["subject"] == slot_sha256
        ]
        if len(found) != 1:
            raise ValueError("PI04 certificate missing or duplicated")
        return cast(dict[str, Any], found[0])

    def publish(self, content: ForecastContent, *, attempt_id: str) -> dict[str, Any]:
        content.__post_init__()
        if (
            content.slot.stream.evidence_class == "forward-shadow"
            and self.admission is None
        ):
            raise ValueError(
                "PI04 forward publication requires trusted-owner admission"
            )
        identifier(attempt_id)
        if len(attempt_id) != 32:
            raise ValueError("Invalid PI04 attempt fence")
        self._load()
        existing = [
            e
            for e in self._events
            if e["kind"] == "publication" and e["subject"] == content.slot.sha256
        ]
        if existing:
            accepted = existing[0]["body"]["content_sha256"]
            if accepted == content.sha256:
                return self.receipt(content.slot.sha256)
            conflict = {
                "disposition": "conflict",
                "accepted_content_sha256": accepted,
                "rejected_content_sha256": content.sha256,
                "attempt_id": attempt_id,
            }
            if not any(
                e["kind"] == "conflict" and e["body"] == conflict for e in self._events
            ):
                self._append("conflict", content.slot.sha256, conflict)
            return conflict
        manifest = asdict(content.slot.stream)
        stream_subject = identity(content.slot.stream.stream_id)
        streams = [
            e["body"] for e in self.events("stream") if e["subject"] == stream_subject
        ]
        if streams and streams != [manifest]:
            conflict = {
                "disposition": "conflict",
                "reason": "stream-admission-conflict",
                "accepted_stream_sha256": identity(streams[0]),
                "rejected_stream_sha256": identity(manifest),
            }
            self._append("conflict", content.slot.sha256, conflict)
            return conflict
        if not streams:
            self._append("stream", stream_subject, manifest)
        before = self.clock.sample()
        self._append(
            "publication",
            content.slot.sha256,
            {
                "content": content.body(),
                "content_sha256": content.sha256,
                "attempt_id": attempt_id,
            },
        )
        after = (
            self.clock.sample()
        )  # Publication COMMIT has returned with synchronous=FULL.
        receipt = self._receipt_body(
            content, timeliness(before, after, content.slot), asdict(after)
        )
        self._append("receipt", content.slot.sha256, receipt)
        return receipt

    def status(self) -> dict[str, Any]:
        self._load()
        return {
            "publications": sum(e["kind"] == "publication" for e in self._events),
            "conflicts": sum(e["kind"] == "conflict" for e in self._events),
            "events": len(self._events),
            "bytes": self._bytes,
            "head_sha256": identity(self._events[-1]) if self._events else "0" * 64,
            "forward_source": FORWARD_SOURCE
            if self.admission is not None
            else "read-only-no-forward-grant",
            "final_access": False,
        }
