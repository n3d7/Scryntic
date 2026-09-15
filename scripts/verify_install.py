"""Verify installed bytes from an independent source manifest in isolated Python."""

import hashlib
import importlib.util
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

import scryntic


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    package = Path(scryntic.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), package
    dist = distribution("scryntic")
    assert dist.version == manifest["version"]
    assert dist.metadata["Requires-Python"].replace(" ", "") == manifest[
        "requires_python"
    ].replace(" ", "")
    assert dist.metadata["License-Expression"] == "Apache-2.0"
    for relative, expected in manifest["files"].items():
        installed = package.parent / relative
        assert installed.is_file(), f"Missing installed resource: {relative}"
        assert hashlib.sha256(installed.read_bytes()).hexdigest() == expected, relative
    license_files = [
        f for f in dist.files or [] if str(f).endswith(".dist-info/licenses/LICENSE")
    ]
    assert len(license_files) == 1, "Missing distribution license"
    assert (
        hashlib.sha256(
            Path(str(dist.locate_file(license_files[0]))).read_bytes()
        ).hexdigest()
        == manifest["license_sha256"]
    )
    for module in manifest["forbidden_modules"]:
        assert importlib.util.find_spec(module) is None, module
    print(f"Verified installed scryntic {dist.version}: {len(manifest['files'])} files")


if __name__ == "__main__":
    main()
