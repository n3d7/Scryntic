"""Audit locked closures without installing or resolving packages through pip."""

import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def expected_packages(requirements: str) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        specifiers = list(requirement.specifier)
        if requirement.url or len(specifiers) != 1 or specifiers[0].operator != "==":
            raise ValueError(
                f"Audit requires an exact registry pin: {requirement.name}"
            )
        expected.add((canonicalize_name(requirement.name), specifiers[0].version))
    return expected


def validate_report(report: object, expected: set[tuple[str, str]]) -> None:
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        raise ValueError("Invalid audit report")
    actual: set[tuple[str, str]] = set()
    for dep in report["dependencies"]:
        if not isinstance(dep, dict) or "skip_reason" in dep or dep.get("vulns") != []:
            raise ValueError(
                "Skipped dependency, missing result or vulnerability in audit"
            )
        name, version = dep.get("name"), dep.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("Missing audited identity")
        identity = (canonicalize_name(name), version)
        if identity in actual:
            raise ValueError("Duplicate audit result")
        actual.add(identity)
    if actual != expected:
        raise ValueError("Audit report does not cover the resolved profile exactly")


def main() -> None:
    subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix="scryntic-audit-") as scratch:
        temp = Path(scratch)
        for profile in ("base", "collector", "analysis", "dev", "build", "uv"):
            requirements = temp / f"{profile}.txt"
            if profile == "build":
                requirements.write_text((ROOT / "build-constraints.txt").read_text())
            elif profile == "uv":
                config = tomllib.loads((ROOT / "pyproject.toml").read_text())
                pin = config["tool"]["uv"]["required-version"]
                if not re.fullmatch(r"==[0-9]+\.[0-9]+\.[0-9]+", pin):
                    raise ValueError("Expected exact uv tooling pin")
                requirements.write_text(f"uv{pin}\n")
            else:
                args = [
                    "uv",
                    "export",
                    "--locked",
                    "--no-default-groups",
                    "--no-emit-project",
                    "--no-annotate",
                    "--no-header",
                    "--format",
                    "requirements-txt",
                    "--output-file",
                    str(requirements),
                ]
                if profile != "base":
                    args += ["--group", profile]
                subprocess.run(args, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            expected = expected_packages(requirements.read_text())
            output = temp / f"{profile}.json"
            # Empty profiles have no third-party packages to query, but still get evidence.
            if expected:
                result = subprocess.run(
                    [
                        "pip-audit",
                        "--no-deps" if profile == "uv" else "--require-hashes",
                        "--disable-pip",
                        "--strict",
                        "--progress-spinner",
                        "off",
                        "--format",
                        "json",
                        "--requirement",
                        str(requirements),
                        "--output",
                        str(output),
                    ],
                    cwd=ROOT,
                    check=False,
                )
                if result.returncode != 0 and output.exists():
                    print(output.read_text(), flush=True)
                result.check_returncode()
                report = json.loads(output.read_text())
            else:
                report = {"dependencies": [], "fixes": []}
            validate_report(report, expected)
            print(json.dumps({"profile": profile, "audit": report}), flush=True)


if __name__ == "__main__":
    main()
