"""Build distributables and validate clean, locked profile installations."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package_root = ROOT / "src/scryntic"
    files = {
        p.relative_to(package_root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in package_root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    with tempfile.TemporaryDirectory(prefix="scryntic-package-") as scratch:
        temp = Path(scratch)
        subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=True)
        subprocess.run(
            [
                "uv",
                "build",
                "--no-sources",
                "--force-pep517",
                "--build-constraints",
                "build-constraints.txt",
                "--require-hashes",
                "--out-dir",
                str(temp / "dist"),
            ],
            cwd=ROOT,
            check=True,
        )
        (wheel,) = (temp / "dist").glob("*.whl")
        (sdist,) = (temp / "dist").glob("*.tar.gz")
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                str(sdist),
                "--no-sources",
                "--force-pep517",
                "--build-constraints",
                str(ROOT / "build-constraints.txt"),
                "--require-hashes",
                "--out-dir",
                str(temp / "rebuilt"),
            ],
            cwd=ROOT,
            check=True,
        )
        assert wheel.read_bytes() == (temp / "rebuilt" / wheel.name).read_bytes()
        for artifact in (wheel, sdist):
            print(
                artifact.name,
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                flush=True,
            )
        for profile in ("base", "collector", "analysis"):
            env_path = temp / profile
            env = os.environ | {"UV_PROJECT_ENVIRONMENT": str(env_path)}
            env.pop("VIRTUAL_ENV", None)
            args = [
                "uv",
                "sync",
                "--locked",
                "--no-default-groups",
                "--no-install-project",
            ]
            if profile != "base":
                args += ["--group", profile]
            subprocess.run(args, cwd=ROOT, env=env, check=True)
            python = str(env_path / "bin/python")
            subprocess.run(
                ["uv", "pip", "install", "--python", python, "--no-deps", str(wheel)],
                cwd=temp,
                check=True,
            )
            forbidden = ["pytest", "ruff", "mypy", "pip_audit"]
            if profile != "analysis":
                forbidden += ["torch", "transformers", "timesfm", "huggingface_hub"]
            manifest = temp / "expected.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": config["project"]["version"],
                        "requires_python": config["project"]["requires-python"],
                        "files": files,
                        "license_sha256": hashlib.sha256(
                            (ROOT / "LICENSE").read_bytes()
                        ).hexdigest(),
                        "forbidden_modules": forbidden,
                    }
                )
            )
            subprocess.run(
                [python, "-I", str(ROOT / "scripts/verify_install.py"), str(manifest)],
                cwd=temp,
                check=True,
            )
    print("Packaging and installed profile checks passed", file=sys.stdout)


if __name__ == "__main__":
    main()
