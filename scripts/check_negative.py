"""Demonstrate gate rejection using disposable copies, never broken repository files."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".venv/bin"


def main() -> None:
    controls = {
        "test": ([str(BIN / "python"), "-m", "pytest"], "FAILED"),
        "lint": ([str(BIN / "ruff"), "check", "."], "F821"),
        "format": (
            [str(BIN / "ruff"), "format", "--check", "."],
            "would be reformatted",
        ),
        "type": ([str(BIN / "mypy"), "--no-incremental"], "return-value"),
        "lock": (["uv", "sync", "--locked", "--no-default-groups"], "lockfile"),
        "resource": (
            [str(BIN / "python"), "scripts/check_package.py"],
            "Missing installed resource: py.typed",
        ),
        "audit-unavailable": (
            [str(BIN / "python"), "scripts/audit.py"],
            "audit service unavailable",
        ),
    }
    for kind, (command, expected) in controls.items():
        with tempfile.TemporaryDirectory(
            prefix=f"scryntic-negative-{kind}-"
        ) as scratch:
            temp = Path(scratch)
            work = temp / "source"
            shutil.copytree(
                ROOT,
                work,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".mypy_cache",
                    ".ruff_cache",
                    ".pytest_cache",
                    "dist",
                    "build",
                ),
            )
            config = work / "pyproject.toml"
            target = work / "src/scryntic/negative_control.py"
            env = os.environ | {"UV_OFFLINE": "1"}
            if kind == "test":
                target = work / "tests/test_negative_control.py"
                target.write_text(
                    "def test_deliberate_failure() -> None:\n    assert False\n"
                )
            elif kind == "lint":
                target.write_text("print(undefined_name)\n")
            elif kind == "format":
                target.write_text("value=1\n")
            elif kind == "type":
                target.write_text('def broken() -> int:\n    return "wrong"\n')
            elif kind == "lock":
                # Stale metadata triggers resolution before locked sync refuses
                # the update. Prove rejection without relying on cached indexes.
                env["UV_OFFLINE"] = "0"
                env["UV_CACHE_DIR"] = str(temp / "uv-cache")
                config.write_text(
                    config.read_text().replace(
                        'version = "0.1.0.dev0"',
                        'version = "0.1.0.dev1"',
                        1,
                    )
                )
            elif kind == "resource":
                config.write_text(
                    config.read_text().replace(
                        "[tool.uv.build-backend]",
                        '[tool.uv.build-backend]\nwheel-exclude = ["scryntic/py.typed"]',
                    )
                )
            elif kind == "audit-unavailable":
                fake_bin = temp / "bin"
                fake_bin.mkdir()
                fake_audit = fake_bin / "pip-audit"
                fake_audit.write_text(
                    "#!/bin/sh\necho 'audit service unavailable' >&2\nexit 9\n"
                )
                fake_audit.chmod(0o755)
                env["PATH"] = f"{fake_bin}:{env['PATH']}"
            before = {p: p.read_bytes() for p in (config, work / "uv.lock")}
            if target.exists():
                before[target] = target.read_bytes()
            result = subprocess.run(
                command,
                cwd=work,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if result.returncode == 0 or expected not in result.stdout:
                raise RuntimeError(
                    f"{kind} control did not fail as expected:\n{result.stdout}"
                )
            assert all(p.read_bytes() == content for p, content in before.items())
            print(
                f"PASS: {kind} rejected (exit {result.returncode}); inputs unchanged",
                flush=True,
            )


if __name__ == "__main__":
    main()
