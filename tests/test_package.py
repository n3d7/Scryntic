"""Check the installed package, without adding src to Python's import path."""

import tomllib
from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

import scryntic


def test_package_metadata_and_typing_marker() -> None:
    assert scryntic.__doc__
    assert metadata("scryntic")["License-Expression"] == "Apache-2.0"
    assert files("scryntic").joinpath("py.typed").read_bytes() == b""


def test_collector_profile_pins_supported_pyarrow() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text())
    assert config["dependency-groups"]["collector"] == ["pyarrow==25.0.1"]
