"""Check the installed package, without adding src to Python's import path."""

from importlib.metadata import metadata
from importlib.resources import files

import scryntic


def test_package_metadata_and_typing_marker() -> None:
    assert scryntic.__doc__
    assert metadata("scryntic")["License-Expression"] == "Apache-2.0"
    assert files("scryntic").joinpath("py.typed").read_bytes() == b""
