"""Durable, query-bound continuation for one Bybit historical backfill."""

from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from scryntic.application.sources import BYBIT_CANDLE_SCHEMA, HistoryRequest
from scryntic.domain.identity import InstrumentId
from scryntic.sources.bybit import _INTERVAL_NS, BybitError, _decode_cursor

_MAX_FILE_BYTES = 8_192


@dataclass(frozen=True, slots=True)
class BybitProgress:
    cursor: bytes | None
    complete: bool


def _query_id(request: HistoryRequest, interval: str) -> str:
    subject = request.stream.subject
    if (
        not isinstance(subject, InstrumentId)
        or subject.venue != "bybit"
        or request.stream.schema != BYBIT_CANDLE_SCHEMA
        or interval not in _INTERVAL_NS
    ):
        raise BybitError("Unsupported Bybit history checkpoint request")
    query = json.dumps(
        [
            subject.category,
            subject.symbol,
            interval,
            request.start_ns,
            request.end_ns,
            request.page_size,
        ],
        separators=(",", ":"),
    ).encode("ascii")
    return sha256(query).hexdigest()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate checkpoint field")
        result[key] = value
    return result


class BybitCursorStore:
    """One checkpoint file per history request; the caller saves after page acceptance."""

    def __init__(self, path: Path, *, interval: str = "1") -> None:
        if interval not in _INTERVAL_NS:
            raise ValueError("Unsupported fixed-duration Bybit interval")
        self._path = path
        self._interval = interval

    def load(self, request: HistoryRequest) -> BybitProgress | None:
        query_id = _query_id(request, self._interval)
        flags = os.O_RDONLY | os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise BybitError("Invalid Bybit checkpoint")
            with os.fdopen(descriptor, "rb", closefd=False) as checkpoint:
                raw = checkpoint.read(_MAX_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_FILE_BYTES:
            raise BybitError("Invalid Bybit checkpoint")
        try:
            document = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, ValueError, RecursionError):
            raise BybitError("Invalid Bybit checkpoint") from None
        if (
            type(document) is not dict
            or set(document) != {"version", "query", "complete", "cursor"}
            or type(document["version"]) is not int
            or document["version"] != 1
            or type(document["query"]) is not str
            or document["query"] != query_id
            or type(document["complete"]) is not bool
        ):
            raise BybitError("Bybit checkpoint does not match request")
        if document["complete"]:
            if document["cursor"] is not None:
                raise BybitError("Invalid Bybit checkpoint")
            return BybitProgress(None, True)
        if type(document["cursor"]) is not str:
            raise BybitError("Invalid Bybit checkpoint")
        try:
            cursor = base64.b64decode(document["cursor"], validate=True)
            _decode_cursor(cursor, request, self._interval)
        except (ValueError, UnicodeError):
            raise BybitError("Invalid Bybit checkpoint") from None
        return BybitProgress(cursor, False)

    def save(self, request: HistoryRequest, cursor: bytes | None) -> None:
        query_id = _query_id(request, self._interval)
        if cursor is not None:
            current = _decode_cursor(cursor, request, self._interval)
        previous = self.load(request)
        if previous is not None:
            if previous.complete and cursor is not None:
                raise BybitError("Completed Bybit checkpoint cannot regress")
            if previous.cursor is not None and cursor is not None:
                before = _decode_cursor(previous.cursor, request, self._interval)
                if current.before_ms > before.before_ms:
                    raise BybitError("Bybit checkpoint cannot regress")
        payload = json.dumps(
            {
                "version": 1,
                "query": query_id,
                "complete": cursor is None,
                "cursor": None
                if cursor is None
                else base64.b64encode(cursor).decode("ascii"),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if len(payload) > _MAX_FILE_BYTES:
            raise BybitError("Bybit checkpoint exceeds configured limit")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.", dir=self._path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as checkpoint:
                checkpoint.write(payload)
                checkpoint.flush()
                os.fsync(checkpoint.fileno())
            os.replace(temporary, self._path)
            parent = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
