"""Qualification-only fixed decoder; no product query/runtime selection."""

import os
import sysconfig
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scryntic.archive.canonical import canonical_json_bytes
from scryntic.dataset.protocol import MAX_RESULT_BYTES, primitive_document
from scryntic.imports.filesystem import sealed_bytes
from scryntic.imports.launcher import LinuxDecoder, bounded_process
from scryntic.imports.protocol import ImportError

CANDIDATES = ("pyarrow-pages", "pyarrow-scan", "duckdb-2.0.0")


class ScanDecoder(LinuxDecoder):
    def command(
        self, descriptors: tuple[int, ...], names: tuple[str, ...], candidate: str
    ) -> list[str]:
        if candidate not in CANDIDATES:
            raise ValueError("Unregistered scan candidate")
        command = self._command(descriptors, probe=False, analytical_names=names)
        command[command.index("analysis")] = "pi05"
        if candidate == "duckdb-2.0.0":
            if version("duckdb") != "2.0.0":
                raise ImportError("Unregistered candidate runtime")
            packages = Path(sysconfig.get_path("purelib"))
            native = tuple(packages.glob("_duckdb*.so"))
            if len(native) != 1 or not native[0].is_file() or native[0].is_symlink():
                raise ImportError("Unregistered candidate closure")
            package = packages / "duckdb"
            if not package.is_dir() or package.is_symlink():
                raise ImportError("Unregistered candidate closure")
            at = command.index("--remount-ro")
            command[at:at] = [
                "--ro-bind",
                str(native[0]),
                "/packages/" + native[0].name,
                "--ro-bind",
                str(package),
                "/packages/duckdb",
            ]
        return command

    def qualify(
        self, request: dict[str, Any], manifest: bytes, parquet: bytes, candidate: str
    ) -> dict[str, Any]:
        if candidate not in CANDIDATES:
            raise ValueError("Unregistered scan candidate")
        message = canonical_json_bytes(request | {"candidate": candidate})
        with ExitStack() as resources:
            descriptors = tuple(
                sealed_bytes(data) for data in (message, manifest, parquet)
            )
            for fd in descriptors:
                resources.callback(os.close, fd)
            output = bounded_process(
                self.command(
                    descriptors, ("request", "manifest", "parquet"), candidate
                ),
                descriptors,
                self.limits,
                max_output_bytes=MAX_RESULT_BYTES,
            )
        response = primitive_document(output, MAX_RESULT_BYTES)
        from hashlib import sha256

        if response.get("request_sha256") != sha256(message).hexdigest():
            raise ImportError("Qualification response identity mismatch")
        return response
