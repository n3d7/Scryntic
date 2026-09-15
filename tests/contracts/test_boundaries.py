"""Executable import/type fences; temporary invalid examples never enter src."""

import ast
import dataclasses
import importlib
import inspect
import subprocess
import sys
import types
import typing
from decimal import Decimal
from enum import Enum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/scryntic"
SAFE_STDLIB = {
    "collections",
    "dataclasses",
    "decimal",
    "enum",
    "hashlib",
    "itertools",
    "math",
    "re",
    "typing",
}


def check_imports(source: str, layer: str) -> None:
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "Use explicit imports for the layer fence"
            modules = [node.module or ""]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__", "eval", "exec"}, (
                "Dynamic loading is not a contract"
            )
        for module in modules:
            if module.split(".")[0] in SAFE_STDLIB:
                continue
            assert module.startswith("scryntic.domain.") or (
                layer == "application" and module.startswith("scryntic.application.")
            ), f"Forbidden {layer} dependency: {module}"


def test_owned_contract_imports_point_inward() -> None:
    for layer in ("domain", "application"):
        for path in (SOURCE / layer).rglob("*.py"):
            check_imports(path.read_text(), layer)
    check_imports((SOURCE / "__init__.py").read_text(), "domain")


@pytest.mark.parametrize(
    "source",
    [
        "import bybit",
        "from scryntic.sources.bybit import Client",
        "from scryntic.interfaces.cli import main",
        "from argparse import Namespace",
        "from scryntic.application.dto import CollectRequest",
        "from sqlite3 import Connection",
        "if False:\n    import torch",
        "client = __import__('vendor')",
    ],
)
def test_import_fence_rejects_outer_layers_even_in_dormant_code(source: str) -> None:
    with pytest.raises(AssertionError):
        check_imports(source, "domain")


def check_value_type(annotation: object, visited: set[object]) -> None:
    if annotation in visited:
        return
    visited.add(annotation)
    if isinstance(annotation, typing.TypeAliasType):
        check_value_type(annotation.__value__, visited)
        return
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        assert all(
            type(value) in (str, int, bool) for value in typing.get_args(annotation)
        )
        return
    if origin is not None:
        assert origin in (tuple, frozenset, typing.Union, types.UnionType), (
            f"Infrastructure/container type: {annotation}"
        )
        for arg in typing.get_args(annotation):
            if arg is not Ellipsis:
                check_value_type(arg, visited)
        return
    if isinstance(annotation, dataclasses.InitVar):
        check_value_type(annotation.type, visited)
        return
    if annotation in (str, int, bool, bytes, float, Decimal, type(None)):
        return
    assert inspect.isclass(annotation), f"Non-value type: {annotation}"
    assert annotation.__module__.startswith(
        ("scryntic.domain.", "scryntic.application.")
    )
    if issubclass(annotation, Enum):
        return
    assert dataclasses.is_dataclass(annotation), (
        f"Infrastructure-specific class: {annotation}"
    )
    for field_type in typing.get_type_hints(annotation).values():
        check_value_type(field_type, visited)


def test_public_data_objects_contain_only_owned_values() -> None:
    for layer in ("domain", "application"):
        for path in (SOURCE / layer).glob("*.py"):
            module = importlib.import_module(f"scryntic.{layer}.{path.stem}")
            for value in vars(module).values():
                if inspect.isclass(value) and dataclasses.is_dataclass(value):
                    check_value_type(value, set())


@pytest.mark.parametrize("bad_type", [Path, dict[str, object], object])
def test_dto_fence_rejects_infrastructure_and_untyped_values(bad_type: object) -> None:
    with pytest.raises(AssertionError):
        check_value_type(bad_type, set())


def typecheck(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "contract_consumer.py"
    path.write_text(source)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "cache"),
            str(path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_mypy_accepts_structural_fakes(tmp_path: Path) -> None:
    result = typecheck(
        tmp_path,
        """
from scryntic.application.clock import Clock
from scryntic.application.providers import ForecastProvider
from scryntic.application.sources import StreamingSource
from tests.contracts.fakes import FakeClock, FakeProvider, FakeSource
clock: Clock = FakeClock()
source: StreamingSource = FakeSource()
provider: ForecastProvider = FakeProvider()
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source, diagnostic",
    [
        (
            """
from scryntic.application.clock import Clock
class InvalidClock:
    def sample(self) -> int:
        return 1
clock: Clock = InvalidClock()
""",
            "assignment",
        ),
        (
            """
from scryntic.application.providers import ForecastProvider
from tests.contracts.fakes import FakeProvider
provider: ForecastProvider = FakeProvider()
provider.forecast('not a request')
""",
            "arg-type",
        ),
        (
            """
from scryntic.domain.market import Instrument
from scryntic.domain.identity import InstrumentId
Instrument(InstrumentId('v', 'spot', 'x'), 'a', 'b', 0.1, 0.1, 'a', 'r1')
""",
            "arg-type",
        ),
        (
            """
from scryntic.application.sources import StreamingSource
from tests.contracts.fakes import FakeSource
class InvalidSource(FakeSource):
    async def stream(self, request: str) -> bytes:
        return b''
source: StreamingSource = InvalidSource()
""",
            "override",
        ),
    ],
)
def test_mypy_rejects_invalid_contracts(
    tmp_path: Path, source: str, diagnostic: str
) -> None:
    result = typecheck(tmp_path, source)
    assert result.returncode != 0
    assert f"[{diagnostic}]" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source, diagnostic",
    [
        (
            """
from scryntic.application.providers import ForecastProvider, ForecastRequest
from tests.contracts.fakes import FakeProvider
class InvalidProvider(FakeProvider):
    async def forecast(self, request: ForecastRequest) -> bytes:
        return b'vendor output'
provider: ForecastProvider = InvalidProvider()
""",
            "override",
        ),
        (
            """
from scryntic.application.archive import RawArchive
async def unsafe_read(archive: RawArchive) -> None:
    await archive.read('/tmp/raw', None)
""",
            "arg-type",
        ),
    ],
)
def test_mypy_rejects_provider_and_archive_type_leaks(
    tmp_path: Path, source: str, diagnostic: str
) -> None:
    result = typecheck(tmp_path, source)
    assert result.returncode != 0
    assert f"[{diagnostic}]" in result.stdout, result.stdout + result.stderr
