"""Fixed replay configuration, synthetic clock and feature-only reader contract."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.domain.validation import integer

Partition = Literal["train", "validation", "test"]
Series = tuple[str, str, str, int]


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    start_ns: int
    validation_start_ns: int
    test_start_ns: int
    end_ns: int
    mode: Literal["historical-reconstruction", "as-observed"] = (
        "historical-reconstruction"
    )
    decision_delay_ns: int = 0
    decision_uncertainty_ns: int = 0
    coverage: Literal["include", "exclude"] = "exclude"
    time_quality: Literal["include", "exclude"] = "exclude"

    def __post_init__(self) -> None:
        bounds = (
            self.start_ns,
            self.validation_start_ns,
            self.test_start_ns,
            self.end_ns,
        )
        for value in bounds:
            integer(value)
        if not all(
            left < right for left, right in zip(bounds, bounds[1:], strict=False)
        ):
            raise ValueError("Replay ranges must be strictly increasing")
        integer(self.decision_delay_ns, 0)
        integer(self.decision_uncertainty_ns, 0)
        if self.mode not in ("historical-reconstruction", "as-observed"):
            raise ValueError("Unsupported replay mode")
        if self.coverage not in ("include", "exclude") or self.time_quality not in (
            "include",
            "exclude",
        ):
            raise ValueError("Unsupported replay quality policy")

    def projection(self) -> dict[str, Any]:
        return asdict(self)

    def partition(
        self, earliest_ns: int, latest_ns: int
    ) -> tuple[Partition, int] | None:
        windows: tuple[tuple[Partition, int, int], ...] = (
            ("train", self.start_ns, self.validation_start_ns),
            ("validation", self.validation_start_ns, self.test_start_ns),
            ("test", self.test_start_ns, self.end_ns),
        )
        for name, start, end in windows:
            if start <= earliest_ns <= latest_ns < end:
                return name, end
        return None

    @staticmethod
    def label_fits(label_end_ns: int, available_ns: int, boundary_ns: int) -> bool:
        # A label touching the boundary is conservatively purged too.
        return label_end_ns < boundary_ns and available_ns < boundary_ns


class ReplayClock:
    """Caller-driven logical time; no sleep, network or physical-clock reads."""

    def __init__(self, now_ns: int, *, uncertainty_ns: int = 0) -> None:
        integer(now_ns)
        integer(uncertainty_ns, 0)
        self._now_ns = now_ns
        self._uncertainty_ns = uncertainty_ns

    @property
    def sample(self) -> ClockSample:
        return ClockSample(
            self._now_ns,
            0,
            "f19-replay",
            TimeQuality("synthetic-decision-v1", "healthy", 0, self._uncertainty_ns, 0),
        )

    def advance(self, now_ns: int) -> ClockSample:
        integer(now_ns)
        if now_ns < self._now_ns:
            raise ValueError("Replay clock cannot move backward")
        self._now_ns = now_ns
        return self.sample


@dataclass(frozen=True, slots=True)
class ReplayFeatures:
    row_index: int
    series: Series
    decision_ns: int
    values: tuple[Decimal, ...]
    flags: tuple[str, ...]


class ReplayReader(Protocol):
    """Family readers expose only information available at the supplied clock."""

    def features(self, index: int, clock: ClockSample) -> ReplayFeatures | None: ...
