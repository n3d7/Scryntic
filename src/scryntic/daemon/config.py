"""Explicit foreground profile and bounded runtime configuration."""

import math
import re
from dataclasses import dataclass
from typing import Literal

from scryntic.configuration.paths import Installation
from scryntic.domain.validation import identifier


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    installation: Installation
    producer: str = "daemon"
    epoch: str = "daemon-v1"
    profile: Literal["bybit", "fixture"] = "bybit"
    category: Literal["spot", "linear", "inverse"] = "spot"
    symbol: str = "BTCUSDT"
    queue_capacity: int = 64
    max_payload_bytes: int = 8192
    storage_reserve_bytes: int = 16 * 1024 * 1024
    pipeline_interval_s: float = 0.2
    clock_interval_s: float = 1.0
    sync_interval_s: float = 60.0
    freshness_limit_ns: int = 120_000_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.installation, Installation):
            raise TypeError("Expected installation policy")
        identifier(self.producer)
        identifier(self.epoch)
        if self.profile not in ("bybit", "fixture") or self.category not in (
            "spot",
            "linear",
            "inverse",
        ):
            raise ValueError("Unknown explicit daemon profile")
        if (
            not isinstance(self.symbol, str)
            or re.fullmatch(r"[A-Z0-9]{1,32}", self.symbol, flags=re.ASCII) is None
        ):
            raise ValueError("Invalid daemon instrument symbol")
        for value, maximum in (
            (self.queue_capacity, 4096),
            (self.max_payload_bytes, 65536),
            (self.storage_reserve_bytes, 2**63 - 1),
            (self.freshness_limit_ns, 2**63 - 1),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid bounded daemon capacity")
        for interval in (
            self.pipeline_interval_s,
            self.clock_interval_s,
            self.sync_interval_s,
        ):
            if not math.isfinite(interval) or not 0 < interval <= 300:
                raise ValueError("Invalid daemon refresh interval")
