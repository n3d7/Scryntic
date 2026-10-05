"""Explicit operator trust, retained history and atomic validated pull progress."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import cast

from scryntic.archive.canonical import JsonObject, JsonValue, canonical_json_bytes
from scryntic.configuration.paths import Installation
from scryntic.domain.raw import IngestionId
from scryntic.domain.time import ClockSample
from scryntic.domain.validation import identifier
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportLimits, parse_request
from scryntic.publication.manifest import (
    GENESIS_MANIFEST_HASH,
    ManifestDocument,
    ManifestRef,
)
from scryntic.sync.model import Anchor, Enrollment, PullError, PullLimits

_BACKUP_LIMIT = "Backup limit"
_HISTORY_LIMIT = "History limit"
type _EpochHistory = tuple[Anchor, Anchor, tuple[bytes, ...]]
type _EnrollmentHistory = tuple[Enrollment, list[_EpochHistory]]


def _object(value: JsonValue, keys: set[str]) -> JsonObject:
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("Invalid backup fields")
    return value


def _unique(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate backup field")
        result[key] = value
    return result


def _projection(value: Enrollment | Anchor) -> JsonObject:
    return cast(JsonObject, asdict(value))


class PullCatalog(ImportCatalog):
    """One SQLite owner; restoration names trust without granting conformance."""

    def __enter__(self) -> "PullCatalog":
        self._check()
        return self

    def __init__(
        self,
        installation: Installation,
        limits: ImportLimits | None = None,
        pull_limits: PullLimits | None = None,
    ) -> None:
        self.pull_limits = PullLimits() if pull_limits is None else pull_limits
        self._pending: tuple[str, bytes, float | None] | None = None
        super().__init__(installation, limits)
        try:
            with self._transaction():
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pull_meta (version INTEGER PRIMARY KEY CHECK(version=1)) STRICT"
                )
                row = self._db.execute("SELECT version FROM pull_meta").fetchall()
                if row not in ([], [(1,)]):
                    raise ValueError("Unsupported pull catalog")
                self._db.execute("INSERT OR IGNORE INTO pull_meta VALUES (1)")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pull_enrollments (name TEXT PRIMARY KEY, endpoint BLOB NOT NULL) STRICT"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pull_epochs (name TEXT NOT NULL, epoch TEXT NOT NULL, ordinal INTEGER NOT NULL, bootstrap_sequence INTEGER NOT NULL, bootstrap_hash TEXT NOT NULL, sequence INTEGER NOT NULL, manifest_hash TEXT NOT NULL, PRIMARY KEY(name,epoch), UNIQUE(name,ordinal)) STRICT"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS pull_manifests (name TEXT NOT NULL, epoch TEXT NOT NULL, sequence INTEGER NOT NULL, manifest_hash TEXT NOT NULL, manifest BLOB NOT NULL, PRIMARY KEY(name,epoch,sequence)) STRICT"
                )
        except BaseException as error:
            self.close()
            if isinstance(error, Exception):
                raise PullError("Unable to open pull catalog") from None
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._check()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            try:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
            except BaseException:
                self.close()
            raise

    def enrollment(self, name: str) -> Enrollment:
        self._check()
        try:
            identifier(name)
            row = self._db.execute(
                "SELECT endpoint FROM pull_enrollments WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown enrollment")
            return self._endpoint(json.loads(row[0]))
        except Exception:
            raise PullError("Enrollment unavailable") from None

    @staticmethod
    def _endpoint(value: JsonValue) -> Enrollment:
        item = _object(value, {field.name for field in fields(Enrollment)})
        if type(item["port"]) is not int or any(
            type(raw) is not str for key, raw in item.items() if key != "port"
        ):
            raise ValueError("Invalid endpoint types")
        # Runtime constructors validate types; the JSON annotation is broader.
        return Enrollment(**cast(dict[str, object], item))  # type: ignore[arg-type]

    @staticmethod
    def _anchor(value: JsonValue) -> Anchor:
        item = _object(value, {field.name for field in fields(Anchor)})
        return Anchor(**cast(dict[str, object], item))  # type: ignore[arg-type]

    def epochs(self, name: str) -> tuple[str, ...]:
        self.enrollment(name)
        return tuple(
            row[0]
            for row in self._db.execute(
                "SELECT epoch FROM pull_epochs WHERE name=? ORDER BY ordinal", (name,)
            )
        )

    def _stored_anchor(
        self, name: str, epoch: str | None, *, bootstrap: bool
    ) -> Anchor:
        value = self.enrollment(name)
        selected = value.epoch if epoch is None else epoch
        identifier(selected)
        columns = (
            "bootstrap_sequence,bootstrap_hash"
            if bootstrap
            else "sequence,manifest_hash"
        )
        row = self._db.execute(
            f"SELECT {columns} FROM pull_epochs WHERE name=? AND epoch=?",
            (name, selected),
        ).fetchone()
        if row is None:
            raise PullError("Epoch unavailable")
        return Anchor(value.producer, selected, *row)

    def anchor(self, name: str, epoch: str | None = None) -> Anchor:
        return self._stored_anchor(name, epoch, bootstrap=False)

    def bootstrap(self, name: str, epoch: str) -> Anchor:
        return self._stored_anchor(name, epoch, bootstrap=True)

    def history(self, name: str) -> tuple[bytes, ...]:
        self.enrollment(name)
        return tuple(
            row[0]
            for row in self._db.execute(
                "SELECT m.manifest FROM pull_manifests m JOIN pull_epochs e USING(name,epoch) WHERE m.name=? ORDER BY e.ordinal,m.sequence",
                (name,),
            )
        )

    def is_imported(self, reference: ManifestRef) -> bool:
        self._check()
        row = self._db.execute(
            "SELECT producer,epoch,sequence FROM imports WHERE manifest_hash=?",
            (reference.manifest_hash,),
        ).fetchone()
        return bool(row == (reference.producer, reference.epoch, reference.sequence))

    def _checkpoint(self, value: Enrollment, checkpoint: bytes | None) -> Anchor:
        if checkpoint is None:
            return Anchor(value.producer, value.epoch, 0, GENESIS_MANIFEST_HASH)
        document = parse_request(checkpoint, self.limits)
        if (document.body.producer, document.body.epoch) != (
            value.producer,
            value.epoch,
        ):
            raise ValueError("Checkpoint identity mismatch")
        return Anchor(
            value.producer, value.epoch, document.body.sequence, document.manifest_hash
        )

    def _add_epoch(
        self, value: Enrollment, ordinal: int, checkpoint: bytes | None
    ) -> None:
        bootstrap = self._checkpoint(value, checkpoint)
        if (
            self._db.execute("SELECT count(*) FROM pull_epochs").fetchone()[0]
            >= self.pull_limits.max_entries
        ):
            raise ValueError("Epoch limit")
        self._db.execute(
            "INSERT INTO pull_epochs VALUES (?,?,?,?,?,?,?)",
            (
                value.name,
                value.epoch,
                ordinal,
                bootstrap.sequence,
                bootstrap.manifest_hash,
                bootstrap.sequence,
                bootstrap.manifest_hash,
            ),
        )
        if checkpoint is not None:
            self._retain(value.name, parse_request(checkpoint, self.limits), checkpoint)

    def _retain(self, name: str, document: ManifestDocument, data: bytes) -> None:
        if (
            self._db.execute("SELECT count(*) FROM pull_manifests").fetchone()[0]
            >= self.pull_limits.max_manifests
        ):
            raise ValueError(_HISTORY_LIMIT)
        self._db.execute(
            "INSERT INTO pull_manifests VALUES (?,?,?,?,?)",
            (
                name,
                document.body.epoch,
                document.body.sequence,
                document.manifest_hash,
                data,
            ),
        )

    def enroll(self, value: Enrollment, checkpoint: bytes | None = None) -> None:
        try:
            if not isinstance(value, Enrollment):
                raise TypeError("Expected enrollment")
            self._endpoint(_projection(value))
            bootstrap = self._checkpoint(value, checkpoint)
            with self._transaction():
                row = self._db.execute(
                    "SELECT endpoint FROM pull_enrollments WHERE name=?", (value.name,)
                ).fetchone()
                endpoint = canonical_json_bytes(_projection(value))
                if row is not None:
                    if (
                        row != (endpoint,)
                        or self.bootstrap(value.name, value.epoch) != bootstrap
                    ):
                        raise ValueError("Enrollment conflict")
                    return
                if (
                    self._db.execute(
                        "SELECT count(*) FROM pull_enrollments"
                    ).fetchone()[0]
                    >= self.pull_limits.max_entries
                ):
                    raise ValueError("Enrollment limit")
                self._db.execute(
                    "INSERT INTO pull_enrollments VALUES (?,?)", (value.name, endpoint)
                )
                self._add_epoch(value, 0, checkpoint)
        except Exception:
            raise PullError("Enrollment rejected") from None

    def authorize_epoch(
        self, name: str, epoch: str, checkpoint: bytes | None = None
    ) -> None:
        try:
            identifier(epoch)
            value = replace(self.enrollment(name), epoch=epoch)
            with self._transaction():
                epochs = self.epochs(name)
                if epoch in epochs:
                    raise ValueError("Epoch already authorized")
                self._add_epoch(value, len(epochs), checkpoint)
                self._db.execute(
                    "UPDATE pull_enrollments SET endpoint=? WHERE name=?",
                    (canonical_json_bytes(_projection(value)), name),
                )
        except Exception:
            raise PullError("Epoch authorization rejected") from None

    def _prior_checkpoint(self, name: str, epoch: str) -> IngestionId | None:
        epochs = self.epochs(name)
        index = epochs.index(epoch)
        for previous in reversed(epochs[:index]):
            data = self._db.execute(
                "SELECT manifest FROM pull_manifests WHERE name=? AND epoch=? ORDER BY sequence DESC LIMIT 1",
                (name, previous),
            ).fetchone()
            if data is not None:
                return parse_request(data[0], self.limits).body.checkpoint_after
        return None

    def _next(self, name: str, data: bytes) -> tuple[ManifestDocument, Anchor, bool]:
        value = self.enrollment(name)
        document = parse_request(data, self.limits)
        body = document.body
        if body.producer != value.producer:
            raise PullError("Manifest producer rejected")
        current = self.anchor(name, body.epoch)
        if body.sequence <= current.sequence:
            retained = self._db.execute(
                "SELECT manifest FROM pull_manifests WHERE name=? AND epoch=? AND sequence=?",
                (name, body.epoch, body.sequence),
            ).fetchone()
            if retained != (data,):
                raise PullError("Retained history mismatch")
            return document, current, False
        if (
            body.epoch != value.epoch
            or body.sequence != current.sequence + 1
            or body.previous_manifest_hash != current.manifest_hash
        ):
            raise PullError("Manifest chain rejected")
        expected: IngestionId | None
        if current.sequence:
            retained = self._db.execute(
                "SELECT manifest FROM pull_manifests WHERE name=? AND epoch=? AND sequence=?",
                (name, body.epoch, current.sequence),
            ).fetchone()
            if retained is None:
                raise PullError("Accepted history unavailable")
            expected = parse_request(retained[0], self.limits).body.checkpoint_after
        else:
            expected = self._prior_checkpoint(name, body.epoch)
        if body.checkpoint_before != expected:
            raise PullError("Manifest checkpoint rejected")
        return document, current, True

    def accept_next(
        self,
        name: str,
        data: bytes,
        incoming: Path,
        receipt: ClockSample,
        *,
        deadline: float | None = None,
    ) -> ManifestRef:
        self._check()
        self._deadline(deadline)
        if self._pending is not None:
            raise PullError("Pull registration unavailable")
        self._next(name, data)
        self._deadline(deadline)
        self._pending = (name, data, deadline)
        try:
            return super().accept(data, incoming, receipt)
        finally:
            self._pending = None

    @staticmethod
    def _deadline(deadline: float | None) -> None:
        if deadline is None:
            return
        try:
            if (
                type(deadline) not in (float, int)
                or not isfinite(deadline)
                or monotonic() >= deadline
            ):
                raise ValueError("Pull deadline unavailable")
        except Exception:
            raise PullError("Pull deadline exceeded") from None

    def _register_import(
        self, document: ManifestDocument, data: bytes, receipt_bytes: bytes
    ) -> None:
        if self._pending is None:
            super()._register_import(document, data, receipt_bytes)
            return
        name, expected_data, deadline = self._pending
        self._deadline(deadline)
        if expected_data != data:
            raise PullError("Pull registration mismatch")
        checked, current, advance = self._next(name, data)
        if checked != document:
            raise PullError("Pull registration mismatch")
        self._deadline(deadline)
        super()._register_import(document, data, receipt_bytes)
        if not advance:
            return
        self._retain(name, document, data)
        changed = self._db.execute(
            "UPDATE pull_epochs SET sequence=?,manifest_hash=? WHERE name=? AND epoch=? AND sequence=? AND manifest_hash=?",
            (
                document.body.sequence,
                document.manifest_hash,
                name,
                document.body.epoch,
                current.sequence,
                current.manifest_hash,
            ),
        )
        if changed.rowcount != 1:
            raise PullError("Accepted anchor changed")

    def _snapshot(self) -> JsonObject:
        self._check()
        entries: list[JsonValue] = []
        size = len(canonical_json_bytes({"version": 1, "enrollments": []}))

        def budget(value: JsonValue, comma: bool) -> None:
            nonlocal size
            size += len(canonical_json_bytes(value)) + int(comma)
            if size > self.pull_limits.max_backup_bytes:
                raise ValueError(_BACKUP_LIMIT)

        for row in self._db.execute("SELECT name FROM pull_enrollments ORDER BY name"):
            name = row[0]
            endpoint = _projection(self.enrollment(name))
            budget({"endpoint": endpoint, "epochs": []}, bool(entries))
            epochs: list[JsonValue] = []
            for epoch in self.epochs(name):
                epoch_item: JsonObject = {
                    "bootstrap": _projection(self.bootstrap(name, epoch)),
                    "accepted": _projection(self.anchor(name, epoch)),
                    "manifests": [],
                }
                budget(epoch_item, bool(epochs))
                manifests: list[JsonValue] = []
                for manifest in self._db.execute(
                    "SELECT manifest FROM pull_manifests WHERE name=? AND epoch=? ORDER BY sequence",
                    (name, epoch),
                ):
                    raw = manifest[0].decode("utf-8")
                    budget(raw, bool(manifests))
                    manifests.append(raw)
                epoch_item["manifests"] = manifests
                epochs.append(epoch_item)
            entries.append({"endpoint": endpoint, "epochs": epochs})
        return {"version": 1, "enrollments": entries}

    def backup(self) -> bytes:
        try:
            data = canonical_json_bytes(self._snapshot())
            if len(data) > self.pull_limits.max_backup_bytes:
                raise ValueError(_BACKUP_LIMIT)
            self._parse_backup(data)
            return data
        except Exception:
            raise PullError("Backup rejected") from None

    def _parse_backup(self, data: bytes) -> list[_EnrollmentHistory]:
        if type(data) is not bytes or len(data) > self.pull_limits.max_backup_bytes:
            raise ValueError(_BACKUP_LIMIT)
        root = _object(
            json.loads(data.decode("utf-8"), object_pairs_hook=_unique),
            {"version", "enrollments"},
        )
        if (
            canonical_json_bytes(root) != data
            or type(root["version"]) is not int
            or root["version"] != 1
        ):
            raise ValueError("Unsupported backup")
        entries = root["enrollments"]
        if type(entries) is not list or len(entries) > self.pull_limits.max_entries:
            raise ValueError("Enrollment limit")
        result: list[_EnrollmentHistory] = []
        names: set[str] = set()
        total_epochs = total_manifests = 0
        for entry in entries:
            value, epochs = self._parse_enrollment(
                entry,
                self.pull_limits.max_entries - total_epochs,
                self.pull_limits.max_manifests - total_manifests,
            )
            if value.name in names:
                raise ValueError("Duplicate enrollment")
            names.add(value.name)
            total_epochs += len(epochs)
            total_manifests += sum(len(history) for _, _, history in epochs)
            result.append((value, epochs))
        if [value.name for value, _ in result] != sorted(names):
            raise ValueError("Enrollment order mismatch")
        return result

    def _parse_enrollment(
        self, entry: JsonValue, remaining_epochs: int, remaining_manifests: int
    ) -> _EnrollmentHistory:
        item = _object(entry, {"endpoint", "epochs"})
        value = self._endpoint(item["endpoint"])
        epochs = item["epochs"]
        if type(epochs) is not list or not epochs:
            raise ValueError("Missing epochs")
        if len(epochs) > remaining_epochs:
            raise ValueError(_HISTORY_LIMIT)
        retained: list[_EpochHistory] = []
        seen: set[str] = set()
        prior_checkpoint: IngestionId | None = None
        for epoch_value in epochs:
            history, prior_checkpoint = self._parse_epoch(
                epoch_value, value.producer, prior_checkpoint, remaining_manifests
            )
            bootstrap, _, manifests = history
            if bootstrap.epoch in seen:
                raise ValueError("Epoch identity mismatch")
            seen.add(bootstrap.epoch)
            remaining_manifests -= len(manifests)
            retained.append(history)
        if value.epoch != retained[-1][0].epoch:
            raise ValueError("Active epoch mismatch")
        return value, retained

    def _parse_epoch(
        self,
        value: JsonValue,
        producer: str,
        prior_checkpoint: IngestionId | None,
        remaining_manifests: int,
    ) -> tuple[_EpochHistory, IngestionId | None]:
        item = _object(value, {"bootstrap", "accepted", "manifests"})
        bootstrap = self._anchor(item["bootstrap"])
        accepted = self._anchor(item["accepted"])
        if (
            bootstrap.producer != producer
            or (accepted.producer, accepted.epoch)
            != (bootstrap.producer, bootstrap.epoch)
            or accepted.sequence < bootstrap.sequence
        ):
            raise ValueError("Epoch identity mismatch")
        manifests = item["manifests"]
        if type(manifests) is not list:
            raise ValueError("Invalid retained manifests")
        if len(manifests) > remaining_manifests:
            raise ValueError(_HISTORY_LIMIT)
        history, checkpoint = self._parse_history(
            bootstrap, accepted, manifests, prior_checkpoint
        )
        return (bootstrap, accepted, history), checkpoint

    def _parse_history(
        self,
        bootstrap: Anchor,
        accepted: Anchor,
        manifests: list[JsonValue],
        prior_checkpoint: IngestionId | None,
    ) -> tuple[tuple[bytes, ...], IngestionId | None]:
        current = bootstrap
        checkpoint = prior_checkpoint
        history: list[bytes] = []
        for index, raw in enumerate(manifests):
            if type(raw) is not str:
                raise ValueError("Invalid manifest bytes")
            encoded = raw.encode("utf-8")
            document = parse_request(encoded, self.limits)
            self._history_step(bootstrap, current, checkpoint, index, document)
            body = document.body
            current = Anchor(
                body.producer, body.epoch, body.sequence, document.manifest_hash
            )
            checkpoint = body.checkpoint_after
            history.append(encoded)
        if current != accepted or (bootstrap.sequence and not history):
            raise ValueError("Accepted anchor mismatch")
        return tuple(history), checkpoint

    @staticmethod
    def _history_step(
        bootstrap: Anchor,
        current: Anchor,
        checkpoint: IngestionId | None,
        index: int,
        document: ManifestDocument,
    ) -> None:
        body = document.body
        if (body.producer, body.epoch) != (bootstrap.producer, bootstrap.epoch):
            raise ValueError("Manifest identity mismatch")
        if index == 0 and bootstrap.sequence:
            if document.ref != bootstrap.reference:
                raise ValueError("Bootstrap mismatch")
        elif (
            body.sequence != current.sequence + 1
            or body.previous_manifest_hash != current.manifest_hash
            or body.checkpoint_before != checkpoint
        ):
            raise ValueError("Retained chain mismatch")

    def _restore_enrollment(
        self, value: Enrollment, epochs: list[_EpochHistory], existing: bool
    ) -> None:
        old_epochs: tuple[str, ...] = ()
        if existing:
            old_endpoint = self.enrollment(value.name)
            if replace(old_endpoint, epoch=value.epoch) != value:
                raise ValueError("Endpoint pin conflict")
            old_epochs = self.epochs(value.name)
            if (
                tuple(bootstrap.epoch for bootstrap, _, _ in epochs[: len(old_epochs)])
                != old_epochs
            ):
                raise ValueError("Epoch history conflict")
        else:
            self._db.execute(
                "INSERT INTO pull_enrollments VALUES (?,?)",
                (value.name, canonical_json_bytes(_projection(value))),
            )
        for ordinal, history in enumerate(epochs):
            self._restore_epoch(
                value.name, ordinal, history, history[0].epoch in old_epochs
            )
        self._db.execute(
            "UPDATE pull_enrollments SET endpoint=? WHERE name=?",
            (canonical_json_bytes(_projection(value)), value.name),
        )

    def _missing_history(
        self, name: str, bootstrap: Anchor, accepted: Anchor, history: tuple[bytes, ...]
    ) -> tuple[bytes, ...]:
        current = self.anchor(name, bootstrap.epoch)
        existing = tuple(
            row[0]
            for row in self._db.execute(
                "SELECT manifest FROM pull_manifests WHERE name=? AND epoch=? ORDER BY sequence",
                (name, bootstrap.epoch),
            )
        )
        if (
            self.bootstrap(name, bootstrap.epoch) != bootstrap
            or current.sequence > accepted.sequence
            or existing != history[: len(existing)]
        ):
            raise ValueError("Accepted history rollback")
        if current.sequence == accepted.sequence and current != accepted:
            raise ValueError("Anchor conflict")
        return history[len(existing) :]

    def _restore_epoch(
        self, name: str, ordinal: int, retained: _EpochHistory, existing: bool
    ) -> None:
        bootstrap, accepted, history = retained
        if existing:
            missing = self._missing_history(name, bootstrap, accepted, history)
        else:
            self._db.execute(
                "INSERT INTO pull_epochs VALUES (?,?,?,?,?,?,?)",
                (
                    name,
                    bootstrap.epoch,
                    ordinal,
                    bootstrap.sequence,
                    bootstrap.manifest_hash,
                    bootstrap.sequence,
                    bootstrap.manifest_hash,
                ),
            )
            missing = history
        for raw in missing:
            self._retain(name, parse_request(raw, self.limits), raw)
        self._db.execute(
            "UPDATE pull_epochs SET sequence=?,manifest_hash=? WHERE name=? AND epoch=?",
            (accepted.sequence, accepted.manifest_hash, name, bootstrap.epoch),
        )

    def restore(self, data: bytes) -> None:
        try:
            parsed = self._parse_backup(data)
            with self._transaction():
                restored_names = {value.name for value, _ in parsed}
                existing_names = {
                    row[0]
                    for row in self._db.execute("SELECT name FROM pull_enrollments")
                }
                if not existing_names <= restored_names:
                    raise ValueError("Backup omits enrolled trust")
                for value, epochs in parsed:
                    self._restore_enrollment(
                        value, epochs, value.name in existing_names
                    )
                if (
                    self._db.execute(
                        "SELECT count(*) FROM pull_enrollments"
                    ).fetchone()[0]
                    > self.pull_limits.max_entries
                    or self._db.execute("SELECT count(*) FROM pull_epochs").fetchone()[
                        0
                    ]
                    > self.pull_limits.max_entries
                ):
                    raise ValueError("Restored catalog limit")
        except Exception:
            raise PullError("Restore rejected") from None
