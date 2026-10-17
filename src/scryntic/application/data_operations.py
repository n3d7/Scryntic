"""Typed data and replay requests shared by all application interfaces."""

import re
from dataclasses import dataclass
from typing import Literal, cast

from scryntic.application.operations import Field, Operation, Workflow
from scryntic.domain.validation import digest, immutable_tuple, integer

Mode = Literal["historical-reconstruction", "as-observed"]
Revision = Literal["latest", "first"]
Coverage = Literal["include", "exclude", "require-complete"]
TimeQualityPolicy = Literal["include", "exclude", "require-healthy"]
ReplayQuality = Literal["include", "exclude"]


def _leaf(value: str, *, optional: bool = False) -> None:
    if optional and value == "":
        return
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value
    ):
        raise ValueError("Expected a protected file leaf")


def _choice(value: str, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        raise ValueError("Unsupported data policy")


def _bounded(value: int, minimum: int, maximum: int) -> None:
    integer(value, minimum)
    if value > maximum:
        raise ValueError("Data request exceeds its bounded range")


@dataclass(frozen=True, slots=True)
class DataInspectRequest:
    @property
    def operation(self) -> Operation:
        return Operation.DATA_INSPECT

    manifest_sha256: str

    def __post_init__(self) -> None:
        digest(self.manifest_sha256)


@dataclass(frozen=True, slots=True)
class DatasetBuildRequest:
    @property
    def operation(self) -> Operation:
        return Operation.DATASET_BUILD

    manifests: tuple[str, ...]
    mode: Mode = "historical-reconstruction"
    revision: Revision = "latest"
    coverage: Coverage = "include"
    time_quality: TimeQualityPolicy = "include"
    finalized_only: bool = False
    lag_steps: int = 0
    label_horizon_steps: int = 0
    cutoff_leaf: str = ""
    coverage_leaf: str = ""

    def __post_init__(self) -> None:
        immutable_tuple(self.manifests, 8)
        if not self.manifests or len(set(self.manifests)) != len(self.manifests):
            raise ValueError("Expected distinct accepted input hashes")
        for value in self.manifests:
            digest(value)
        _choice(self.mode, ("historical-reconstruction", "as-observed"))
        _choice(self.revision, ("latest", "first"))
        _choice(self.coverage, ("include", "exclude", "require-complete"))
        _choice(self.time_quality, ("include", "exclude", "require-healthy"))
        if type(self.finalized_only) is not bool:
            raise TypeError("Expected explicit finality policy")
        _bounded(self.lag_steps, 0, 128)
        _bounded(self.label_horizon_steps, 0, 128)
        _leaf(self.cutoff_leaf, optional=True)
        _leaf(self.coverage_leaf, optional=True)
        if (self.mode == "as-observed") != bool(self.cutoff_leaf):
            raise ValueError("Strict selection requires an explicit cutoff capture")


@dataclass(frozen=True, slots=True)
class DatasetInspectRequest:
    @property
    def operation(self) -> Operation:
        return Operation.DATASET_INSPECT

    manifest_sha256: str
    offset: int = 0
    limit: int = 128

    def __post_init__(self) -> None:
        digest(self.manifest_sha256)
        _bounded(self.offset, 0, 100_000)
        _bounded(self.limit, 1, 128)


@dataclass(frozen=True, slots=True)
class DatasetExportRequest:
    @property
    def operation(self) -> Operation:
        return Operation.DATASET_EXPORT

    manifest_sha256: str
    destination: str

    def __post_init__(self) -> None:
        digest(self.manifest_sha256)
        _leaf(self.destination)


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    @property
    def operation(self) -> Operation:
        return Operation.REPLAY

    manifest_sha256: str
    start_ns: int
    validation_start_ns: int
    test_start_ns: int
    end_ns: int
    mode: Mode = "historical-reconstruction"
    decision_delay_ns: int = 0
    decision_uncertainty_ns: int = 0
    coverage: ReplayQuality = "exclude"
    time_quality: ReplayQuality = "exclude"
    destination: str = ""

    def __post_init__(self) -> None:
        digest(self.manifest_sha256)
        bounds = (
            self.start_ns,
            self.validation_start_ns,
            self.test_start_ns,
            self.end_ns,
        )
        for value in bounds:
            _bounded(value, -(2**63), 2**63 - 1)
        if not all(a < b for a, b in zip(bounds, bounds[1:], strict=False)):
            raise ValueError("Expected strictly increasing temporal partitions")
        _bounded(self.decision_delay_ns, 0, 2**63 - 1)
        _bounded(self.decision_uncertainty_ns, 0, 2**63 - 1)
        _choice(self.mode, ("historical-reconstruction", "as-observed"))
        _choice(self.coverage, ("include", "exclude"))
        _choice(self.time_quality, ("include", "exclude"))
        _leaf(self.destination, optional=True)


_HASH = Field("manifest_sha256", "Exact manifest SHA-256", required=True)
_MODE = Field(
    "mode",
    "Timing mode",
    "historical-reconstruction",
    choices=("historical-reconstruction", "as-observed"),
)
_QUALITY = ("include", "exclude")


def _build_dataset(values: dict[str, str]) -> DatasetBuildRequest:
    # Choice validation belongs to the typed request as well as form metadata.
    return DatasetBuildRequest(
        tuple(value.strip() for value in values["manifests"].split(",")),
        mode=cast(Mode, values["mode"]),
        revision=cast(Revision, values["revision"]),
        coverage=cast(Coverage, values["coverage"]),
        time_quality=cast(TimeQualityPolicy, values["time_quality"]),
        finalized_only={"true": True, "false": False}[values["finalized_only"]],
        lag_steps=int(values["lag_steps"]),
        label_horizon_steps=int(values["label_horizon_steps"]),
        cutoff_leaf=values["cutoff_leaf"],
        coverage_leaf=values["coverage_leaf"],
    )


def _replay(values: dict[str, str]) -> ReplayRequest:
    return ReplayRequest(
        values["manifest_sha256"],
        int(values["start_ns"]),
        int(values["validation_start_ns"]),
        int(values["test_start_ns"]),
        int(values["end_ns"]),
        mode=cast(Mode, values["mode"]),
        decision_delay_ns=int(values["decision_delay_ns"]),
        decision_uncertainty_ns=int(values["decision_uncertainty_ns"]),
        coverage=cast(ReplayQuality, values["coverage"]),
        time_quality=cast(ReplayQuality, values["time_quality"]),
        destination=values["destination"],
    )


WORKFLOWS = (
    Workflow(
        Operation.DATA_INSPECT,
        "Inspect accepted data",
        "Data",
        (_HASH,),
        lambda v: DataInspectRequest(v["manifest_sha256"]),
    ),
    Workflow(
        Operation.DATASET_BUILD,
        "Build immutable dataset",
        "Data",
        (
            Field(
                "manifests", "Accepted SHA-256 hashes (comma separated)", required=True
            ),
            _MODE,
            Field("revision", "Revision policy", "latest", choices=("latest", "first")),
            Field(
                "coverage",
                "Coverage policy",
                "include",
                choices=(*_QUALITY, "require-complete"),
            ),
            Field(
                "time_quality",
                "Time quality policy",
                "include",
                choices=(*_QUALITY, "require-healthy"),
            ),
            Field(
                "finalized_only",
                "Finalized candles only",
                "false",
                choices=("false", "true"),
            ),
            Field("lag_steps", "Lag steps (0–128)", "0"),
            Field("label_horizon_steps", "Label horizon (0–128)", "0"),
            Field(
                "cutoff_leaf",
                "Protected cutoff JSON leaf",
                help="Required for as-observed; full clock quality evidence.",
            ),
            Field("coverage_leaf", "Protected coverage capture JSON leaf"),
        ),
        _build_dataset,
    ),
    Workflow(
        Operation.DATASET_INSPECT,
        "Inspect dataset rows",
        "Data",
        (
            _HASH,
            Field("offset", "Row offset", "0"),
            Field("limit", "Rows (1–128)", "128"),
        ),
        lambda v: DatasetInspectRequest(
            v["manifest_sha256"], int(v["offset"]), int(v["limit"])
        ),
    ),
    Workflow(
        Operation.DATASET_EXPORT,
        "Export exact dataset artifacts",
        "Data",
        (_HASH, Field("destination", "New export directory leaf", required=True)),
        lambda v: DatasetExportRequest(v["manifest_sha256"], v["destination"]),
    ),
    Workflow(
        Operation.REPLAY,
        "Evaluate deterministic replay",
        "Replay",
        (
            _HASH,
            Field("start_ns", "Train start (UTC ns)", required=True),
            Field("validation_start_ns", "Validation start (UTC ns)", required=True),
            Field("test_start_ns", "Test start (UTC ns)", required=True),
            Field("end_ns", "End (UTC ns)", required=True),
            _MODE,
            Field("decision_delay_ns", "Decision delay (ns)", "0"),
            Field("decision_uncertainty_ns", "Decision uncertainty (ns)", "0"),
            Field("coverage", "Coverage policy", "exclude", choices=_QUALITY),
            Field("time_quality", "Time quality policy", "exclude", choices=_QUALITY),
            Field("destination", "Optional new report export directory leaf"),
        ),
        _replay,
    ),
)
