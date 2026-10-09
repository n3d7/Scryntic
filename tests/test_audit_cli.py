"""Exercise audit orchestration using local registry reports without network access."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts import audit


def test_audit_profiles_require_complete_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "build-constraints.txt").write_text("Build_Pkg==1\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = "==0.12.13"\n'
    )
    monkeypatch.setattr(audit, "ROOT", tmp_path)

    def registry_command(
        args: list[str], *, cwd: Path, check: bool, stdout: object = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["uv", "export"]:
            output = Path(args[args.index("--output-file") + 1])
            contents = 'Example_Pkg==1\nignored==2; python_version < "3"\n'
            if "--project" in args:
                contents += (
                    "torch==2.14.0+cpu \\\n    --hash=sha256:"
                    + audit.CPU_WHEEL_SHA256
                    + "\n"
                )
            output.write_text(contents)
        elif args[0] == "pip-audit":
            requirements = Path(args[args.index("--requirement") + 1])
            identities = audit.expected_packages(requirements.read_text())
            report = {
                "dependencies": [
                    {"name": name, "version": version, "vulns": []}
                    for name, version in sorted(identities)
                ]
            }
            Path(args[args.index("--output") + 1]).write_text(json.dumps(report))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", registry_command)
    audit.main()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["profile"] for r in records] == [
        "base",
        "collector",
        "analysis",
        "dev",
        "build",
        "uv",
        "forecast-cpu",
    ]
    assert records[4]["audit"]["dependencies"] == [
        {"name": "build-pkg", "version": "1", "vulns": []}
    ]
    assert records[5]["audit"]["dependencies"] == [
        {"name": "uv", "version": "0.12.13", "vulns": []}
    ]
    assert ["torch", "2.14.0+cpu"] in records[6]["locked_identities"]
    assert (
        records[6]["advisory_identity_mapping"]["torch==2.14.0+cpu"] == "torch==2.14.0"
    )


def test_audit_rejects_non_ascii_tooling_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.audit import export_profile

    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = "==١.٢.٣"\n'
    )
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="exact uv"):
        export_profile("uv", tmp_path / "uv.txt")
