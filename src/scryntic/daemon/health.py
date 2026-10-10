"""Bounded health evidence copied from owners without touching their I/O."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import cast

MAX_STREAMS = 64
MAX_JSON_BYTES = 65536
MAX_INTEGER = 2**63 - 1


class State(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


class Fault(StrEnum):
    NONE = "none"
    SOURCE = "source"
    CLOCK = "clock"
    STORAGE = "storage"
    BACKPRESSURE = "backpressure"
    NORMALIZATION = "normalization"
    PUBLICATION = "publication"
    SYNC = "sync"
    JOBS = "jobs"
    SHUTDOWN = "shutdown"
    STALE = "stale"


class ClockStatus(StrEnum):
    UNKNOWN = "unknown"
    DEGRADED = "degraded"
    HEALTHY = "healthy"


class ShutdownPhase(StrEnum):
    NONE = "none"
    INTAKE = "intake"
    DRAIN = "drain"
    CLOSE = "close"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


def _integer(value: int, *, signed: bool = False) -> None:
    if (
        type(value) is not int
        or not (-MAX_INTEGER if signed else 0) <= value <= MAX_INTEGER
    ):
        raise ValueError("health integer is outside its bounded range")


def _boolean(value: bool) -> None:
    if type(value) is not bool:
        raise ValueError("health flag must be a boolean")


@dataclass(frozen=True, slots=True)
class StreamHealth:
    """Stream ordinal is public; timestamps used for freshness are monotonic."""

    ordinal: int
    last_received_monotonic_ns: int = 0
    last_heartbeat_monotonic_ns: int = 0
    last_received_utc_ns: int | None = None
    coverage_start_ns: int = 0
    coverage_end_ns: int = 0
    connected: bool = False
    coverage_pending: int = 0
    coverage_unknown: int = 0
    coverage_unrecoverable: int = 0
    freshness_limit_ns: int = 60_000_000_000
    received_age_ns: int | None = None
    heartbeat_age_ns: int | None = None
    stale: bool = False
    data_stale: bool = False
    heartbeat_stale: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.ordinal,
            self.last_received_monotonic_ns,
            self.last_heartbeat_monotonic_ns,
            self.coverage_start_ns,
            self.coverage_end_ns,
            self.coverage_pending,
            self.coverage_unknown,
            self.coverage_unrecoverable,
            self.freshness_limit_ns,
        ):
            _integer(value)
        if not self.freshness_limit_ns:
            raise ValueError("stream freshness limit must be positive")
        for age in (
            self.received_age_ns,
            self.heartbeat_age_ns,
            self.last_received_utc_ns,
        ):
            if age is not None:
                _integer(age)
        _boolean(self.connected)
        _boolean(self.stale)
        _boolean(self.data_stale)
        _boolean(self.heartbeat_stale)


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    state: State = State.STARTING
    sampled_at_ns: int = 0
    heartbeat_ns: int = 0
    live: bool = True
    ready: bool = False
    streams: tuple[StreamHealth, ...] = ()
    received: int = 0
    durably_committed: int = 0
    normalized: int = 0
    published: int = 0
    clock_status: ClockStatus = ClockStatus.UNKNOWN
    clock_offset_ns: int | None = None
    clock_uncertainty_ns: int | None = None
    clock_evidence_age_ns: int | None = None
    clock_epoch: int = 0
    queue_depth: int = 0
    queue_capacity: int = 0
    backpressure: bool = False
    storage_available_bytes: int = 0
    storage_reserve_bytes: int = 0
    normalization_fault: bool = False
    publication_fault: bool = False
    sync_enabled: bool = False
    sync_healthy: bool = True
    jobs_pending: int = 0
    jobs_running: int = 0
    jobs_failed: int = 0
    jobs_interrupted: int = 0
    jobs_cancelled: int = 0
    jobs_expired: int = 0
    shutdown_intake_stopped: bool = False
    shutdown_pending: int = 0
    shutdown_phase: ShutdownPhase = ShutdownPhase.NONE
    shutdown_deadline_ns: int | None = None
    unfinished_operations: int = 0
    faults: tuple[Fault, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.state) is not State
            or type(self.clock_status) is not ClockStatus
            or type(self.shutdown_phase) is not ShutdownPhase
        ):
            raise ValueError("health status must use a closed enum")
        if (
            type(self.streams) is not tuple
            or len(self.streams) > MAX_STREAMS
            or any(type(stream) is not StreamHealth for stream in self.streams)
        ):
            raise ValueError("health streams must be a bounded typed tuple")
        if len({stream.ordinal for stream in self.streams}) != len(self.streams):
            raise ValueError("health stream ordinals must be unique")
        if (
            type(self.faults) is not tuple
            or len(self.faults) > len(Fault)
            or any(type(fault) is not Fault for fault in self.faults)
            or len(set(self.faults)) != len(self.faults)
        ):
            raise ValueError("health faults must be unique closed enums")
        for value in (
            self.sampled_at_ns,
            self.heartbeat_ns,
            self.received,
            self.durably_committed,
            self.normalized,
            self.published,
            self.clock_epoch,
            self.queue_depth,
            self.queue_capacity,
            self.storage_available_bytes,
            self.storage_reserve_bytes,
            self.jobs_pending,
            self.jobs_running,
            self.jobs_failed,
            self.jobs_interrupted,
            self.jobs_cancelled,
            self.jobs_expired,
            self.shutdown_pending,
            self.unfinished_operations,
        ):
            _integer(value)
        if self.clock_offset_ns is not None:
            _integer(self.clock_offset_ns, signed=True)
        for optional_value in (
            self.clock_uncertainty_ns,
            self.clock_evidence_age_ns,
            self.shutdown_deadline_ns,
        ):
            if optional_value is not None:
                _integer(optional_value)
        for flag in (
            self.live,
            self.ready,
            self.backpressure,
            self.normalization_fault,
            self.publication_fault,
            self.sync_enabled,
            self.sync_healthy,
            self.shutdown_intake_stopped,
        ):
            _boolean(flag)

    def to_json(self) -> bytes:
        """Serialize only the closed public schema, with an absolute size bound."""
        data = json.dumps(
            asdict(self), separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        if len(data) > MAX_JSON_BYTES:
            raise ValueError("health serialization exceeds its size bound")
        return data


class HealthCache:
    """Single owner publishes immutable references; CPython readers never lock.

    This daemon targets CPython 3.12. Reference reads/swaps occur under its GIL;
    callers must publish from the owning event loop, not concurrent writers.
    """

    def __init__(
        self,
        initial: HealthSnapshot | None = None,
        *,
        stale_after_ns: int = 5_000_000_000,
    ) -> None:
        _integer(stale_after_ns)
        if not stale_after_ns:
            raise ValueError("health freshness limit must be positive")
        self._stale_after_ns = stale_after_ns
        self._current = HealthSnapshot() if initial is None else initial
        if type(self._current) is not HealthSnapshot:
            raise ValueError("health cache requires a typed snapshot")

    def publish(self, snapshot: HealthSnapshot) -> None:
        if type(snapshot) is not HealthSnapshot:
            raise ValueError("health cache requires a typed snapshot")
        if snapshot.sampled_at_ns < self._current.sampled_at_ns:
            raise ValueError("health sample time cannot regress")
        self._current = snapshot

    def snapshot(self, now_ns: int | None = None) -> HealthSnapshot:
        now = time.monotonic_ns() if now_ns is None else now_ns
        _integer(now)
        current = self._current
        expired = (
            current.heartbeat_ns == 0
            or now < current.heartbeat_ns
            or now - current.heartbeat_ns > self._stale_after_ns
        )
        streams: list[StreamHealth] = []
        for stream in current.streams:
            received_age = _age(now, stream.last_received_monotonic_ns)
            heartbeat_age = _age(now, stream.last_heartbeat_monotonic_ns)
            latest = max(
                stream.last_received_monotonic_ns, stream.last_heartbeat_monotonic_ns
            )
            data_stale = (
                received_age is None or received_age > stream.freshness_limit_ns
            )
            heartbeat_stale = (
                latest == 0 or now < latest or now - latest > stream.freshness_limit_ns
            )
            stale = not stream.connected or data_stale or heartbeat_stale
            streams.append(
                replace(
                    stream,
                    received_age_ns=received_age,
                    heartbeat_age_ns=heartbeat_age,
                    stale=stale,
                    data_stale=data_stale,
                    heartbeat_stale=heartbeat_stale,
                )
            )
        stale_stream = any(stream.stale for stream in streams)
        faults = list(current.faults)
        for present, fault in ((expired, Fault.STALE), (stale_stream, Fault.SOURCE)):
            if present and fault not in faults:
                faults.append(fault)
        state = current.state
        if state is State.HEALTHY and (expired or stale_stream):
            state = State.DEGRADED
        live = current.live and not expired and state is not State.FAILED
        ready = (
            current.ready
            and live
            and not stale_stream
            and state in (State.HEALTHY, State.DEGRADED)
        )
        updated: object = replace(
            current,
            state=state,
            live=live,
            ready=ready,
            streams=tuple(streams),
            faults=tuple(faults),
        )
        return cast(HealthSnapshot, updated)


def _age(now: int, timestamp: int) -> int | None:
    return now - timestamp if 0 < timestamp <= now else None
