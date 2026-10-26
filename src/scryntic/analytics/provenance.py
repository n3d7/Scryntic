"""Bind measurements to exact admitted synthetic fixtures and executed bytes."""

import hashlib
import json
import stat
import sys
import sysconfig
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scryntic.analytics.fixtures import synthetic_rows
from scryntic.dataset.protocol import MAX_RESULT_BYTES, primitive_document


def file_hash(path: Path, *, maximum: int = MAX_RESULT_BYTES) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValueError("Unsafe qualification artifact")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fixture_identity(path: Path, workload: dict[str, Any]) -> str:
    count, gaps = workload["rows"], workload["gaps"]
    if type(count) is not int or type(gaps) is not bool:
        raise ValueError("Invalid registered workload types")
    data = (path / "fixture.json").read_bytes()
    fixture = primitive_document(data, MAX_RESULT_BYTES)
    if (
        fixture.get("schema") != "scryntic.pi05.synthetic.v1"
        or fixture.get("row_count") != workload["rows"]
        or type(fixture.get("row_count")) is not int
        or fixture.get("gaps") is not workload["gaps"]
    ):
        raise ValueError("Registered workload configuration mismatch")
    expected = [
        {k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()}
        for row in synthetic_rows(count=count, gaps=gaps)
    ]
    if (
        fixture.get("expected_rows") != expected
        or file_hash(path / "manifest.json") != fixture.get("manifest_sha256")
        or file_hash(path / "dataset.parquet") != fixture.get("parquet_sha256")
    ):
        raise ValueError("Registered fixture identity mismatch")
    return hashlib.sha256(data).hexdigest()


def source_identity(source: Path) -> str:
    manifest = json.loads((source / "PI05_SOURCE_MANIFEST.json").read_bytes())
    verified = []
    for item in manifest["files"]:
        path = source / item["path"]
        if (
            source.resolve() not in path.resolve().parents
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError("Executed source identity mismatch")
        verified.append([item["path"], item["sha256"]])
    return hashlib.sha256(
        json.dumps(sorted(verified), separators=(",", ":")).encode()
    ).hexdigest()


def runtime_identity() -> dict[str, str]:
    if version("pyarrow") != "25.0.1" or version("duckdb") != "2.0.0":
        raise ValueError("Qualification runtime version mismatch")
    packages = Path(sysconfig.get_path("purelib"))
    files = [
        p
        for name in ("pyarrow", "duckdb")
        for p in (packages / name).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    files.extend(packages.glob("_duckdb*.so"))
    values = [
        [str(p.relative_to(packages)), file_hash(p, maximum=512 * 1024 * 1024)]
        for p in sorted(files)
    ]
    return {
        "runtime_sha256": hashlib.sha256(
            json.dumps(values, separators=(",", ":")).encode()
        ).hexdigest(),
        "python_sha256": file_hash(Path(sys.executable).resolve()),
    }
