"""Explicit F07 fixtures built only from public F03-F06 values."""

from pathlib import Path

from scryntic.configuration.paths import Installation
from tests.normalization.helpers import installation as normalization_installation


def installation(root: Path) -> Installation:
    return normalization_installation(root)
