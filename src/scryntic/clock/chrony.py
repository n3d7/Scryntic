"""Read-only chrony 4.x tracking CSV adapter; no time administration commands."""

import csv
import math
import os
import re
import subprocess
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from scryntic.application.clock import SyncEvidence

_INVALID_REPORT = "Invalid synchronization report"

_NS = Decimal(1_000_000_000)
_MAX_NS = (1 << 63) - 1


def _number(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(_INVALID_REPORT) from None
    if not value.is_finite() or value.copy_abs() > Decimal("9223372036.854775807"):
        raise ValueError(_INVALID_REPORT)
    return value


def parse_tracking(report: str) -> SyncEvidence:
    """Preserve signed system correction; bound error per the chrony manual."""
    if len(report) > 4096:
        raise ValueError(_INVALID_REPORT)
    try:
        rows = list(csv.reader(report.splitlines(), strict=True))
    except csv.Error:
        raise ValueError(_INVALID_REPORT) from None
    if len(rows) != 1 or len(rows[0]) != 14:
        raise ValueError(_INVALID_REPORT)
    row = rows[0]
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", row[0]) or not re.fullmatch(
        r"[0-9]+", row[2]
    ):
        raise ValueError(_INVALID_REPORT)
    stratum = int(row[2])
    if not 0 <= stratum <= 15 or row[13] not in (
        "Normal",
        "Not synchronised",
        "Insert second",
        "Delete second",
    ):
        raise ValueError(_INVALID_REPORT)
    numbers = [_number(value) for value in row[3:13]]
    reference, correction = numbers[:2]
    delay, dispersion = numbers[7:9]
    # RMS offset, skew, root metrics and update interval cannot be negative.
    if any(numbers[index] < 0 for index in (0, 3, 6, 7, 8, 9)):
        raise ValueError(_INVALID_REPORT)
    reference_ns = int((reference * _NS).to_integral_value(rounding=ROUND_HALF_EVEN))
    # Positive current_correction is 'slow' in human output (chrony client.c).
    offset_ns = -int((correction * _NS).to_integral_value(rounding=ROUND_HALF_EVEN))
    radius = abs(correction) + delay / 2 + dispersion
    # CSV seconds have 9 fractional places. Round outward and allow 2ns for
    # combined offset/root-metric formatting loss; no float arithmetic in policy.
    uncertainty_ns = int((radius * _NS).to_integral_value(rounding=ROUND_CEILING)) + 2
    if reference_ns > _MAX_NS or uncertainty_ns > _MAX_NS:
        raise ValueError(_INVALID_REPORT)
    synchronized = (
        row[13] == "Normal"
        and stratum > 0
        and row[0].upper() not in ("00000000", "7F7F0101")
        and reference_ns > 0
    )
    return SyncEvidence(reference_ns, offset_ns, uncertainty_ns, synchronized)


class ChronyStatus:
    def __init__(
        self, *, executable: Path = Path("/usr/bin/chronyc"), timeout_s: float = 1.0
    ) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Expected positive status timeout")
        self._executable = executable
        self._timeout_s = timeout_s

    def read(self) -> SyncEvidence | None:
        # Force the local monitoring-only UDP interface, even when the caller
        # could otherwise access chronyd's privileged Unix control socket.
        try:
            response = subprocess.run(
                [str(self._executable), "-n", "-c", "-h", "127.0.0.1", "tracking"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="ascii",
                env={**os.environ, "LC_ALL": "C"},
                check=False,
                timeout=self._timeout_s,
            )
            if response.returncode != 0:
                return None
            return parse_tracking(response.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            # Do not expose daemon text, command exception output or source names.
            return None
