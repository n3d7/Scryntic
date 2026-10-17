"""Interface-neutral requests for explicitly enrolled read-only synchronization."""

import re
from dataclasses import dataclass

from scryntic.application.operations import Field, Operation, Workflow
from scryntic.domain.validation import identifier, integer


@dataclass(frozen=True, slots=True)
class SyncStatusRequest:
    name: str = "default"

    @property
    def operation(self) -> Operation:
        return Operation.SYNC_STATUS

    def __post_init__(self) -> None:
        identifier(self.name)


@dataclass(frozen=True, slots=True)
class SyncEnrollRequest:
    descriptor: str

    @property
    def operation(self) -> Operation:
        return Operation.SYNC_ENROLL

    def __post_init__(self) -> None:
        if type(self.descriptor) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.descriptor
        ):
            raise ValueError("Invalid enrollment descriptor name")


@dataclass(frozen=True, slots=True)
class SyncPullRequest(SyncStatusRequest):
    wall_seconds: int = 120
    max_new_manifests: int = 16

    @property
    def operation(self) -> Operation:
        return Operation.SYNC_PULL

    def __post_init__(self) -> None:
        identifier(self.name)
        integer(self.wall_seconds, 1)
        integer(self.max_new_manifests, 1)
        if self.wall_seconds > 600 or self.max_new_manifests > 128:
            raise ValueError("Synchronization limits exceed bounds")


WORKFLOWS = (
    Workflow(
        Operation.SYNC_STATUS,
        "Inspect enrolled synchronization",
        "Synchronization",
        (Field("name", "Enrollment name", "default"),),
        lambda v: SyncStatusRequest(v["name"]),
    ),
    Workflow(
        Operation.SYNC_ENROLL,
        "Enroll a trusted read-only source",
        "Synchronization",
        (
            Field(
                "descriptor",
                "Protected enrollment descriptor name",
                required=True,
                help="Explicit host-key pin and genesis/checkpoint trust in a protected local JSON descriptor.",
            ),
        ),
        lambda v: SyncEnrollRequest(v["descriptor"]),
        confirmation="Trust this explicitly configured source and checkpoint?",
    ),
    Workflow(
        Operation.SYNC_PULL,
        "Pull accepted artifacts",
        "Synchronization",
        (
            Field("name", "Enrollment name", "default"),
            Field("wall_seconds", "Maximum pull time (seconds)", "120"),
            Field("max_new_manifests", "Maximum new manifests", "16"),
        ),
        lambda v: SyncPullRequest(
            v["name"], int(v["wall_seconds"]), int(v["max_new_manifests"])
        ),
    ),
)
