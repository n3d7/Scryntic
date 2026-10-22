# Runtime and build baseline

F01 establishes packaging only. There is no service, CLI or model implementation yet.

## Supported baseline

- CPython **3.12.14**, standard GIL build, pinned in `.python-version`.
  Project metadata supports security patches `>=3.12.14,<3.13`; qualification
  uses the exact pin, not whichever system interpreter happens to be installed.
- Linux x86_64 with glibc is the initial qualification target. The baseline was
  verified on Fedora 44, glibc 2.43, kernel `7.1.10-200.fc44.x86_64`.
  Other distributions and architectures require the same checks and an updated
  artifact inventory before being claimed as qualified deployment targets.
- **uv 0.12.13** is mandatory (`tool.uv.required-version`). Provision it separately
  from the reviewed artifact below; check its SHA-256 before execution. Do not use
  a floating installer or self-update during deployment.
- **uv_build 0.12.13** is the only build dependency and has no Python dependencies.
  Its Linux x86_64 wheel is hash-pinned in `build-constraints.txt`. Other backend
  wheels require explicit review and additional hashes, not disabled checking.

Python 3.12 was selected from current compatibility evidence, not the host's
Python 3.14: [TimesFM 3.0.2](https://github.com/google-research/timesfm/blob/master/pyproject.toml)
declares Python >=3.10; [PyTorch 2.14.0](https://pypi.org/pypi/torch/2.14.0/json),
[PyArrow 25.0.1](https://pypi.org/pypi/pyarrow/25.0.1/json) and
[DuckDB 1.5.5](https://pypi.org/pypi/duckdb/1.5.5/json) publish CPython 3.12 Linux
x86_64 wheels. This is compatibility evidence only: none is installed or adopted
by F01, and model/driver qualification remains with the model integration task.
[Python 3.12.14](https://www.python.org/downloads/release/python-31214/) is a
security release; the 3.12 series receives security fixes until October 2028.
Review new security releases deliberately, update pins/hashes together, and repeat
qualification; pinning must not become a reason to retain vulnerable versions.

## Profiles and installation

`dependencies` contains shared runtime requirements. `collector` is server-only;
`analysis` is the workstation profile; `dev` is development tooling. All are empty
in F01. No group is selected by default. Future ML/provider packages belong in
explicit optional analysis/model groups, never shared or collector requirements.
Keep one `uv.lock` for compatible groups; isolated worker environments are a later
option only if actual dependency conflicts require them.

After provisioning the pinned uv, run from the repository root:

```sh
uv --version
uv python install 3.12.14 --no-bin
uv lock --check
uv sync --locked --no-default-groups --group collector --no-editable
.venv/bin/python -I -c 'import scryntic; print(scryntic.__file__)'
```

Use `--group analysis` instead for the workstation, or omit `--group` for base.
Use `UV_PROJECT_ENVIRONMENT` to keep separate profile environments on one host.
Managed Python is required; automatic interpreter downloads are disabled except
for explicit provisioning. Run installed Python directly in future services,
without invoking installers or resolving dependencies at runtime.

`uv.lock` belongs in version control alongside `pyproject.toml`. Use `uv lock`
only for intentional dependency changes, then review both files. `--frozen`
alone does not detect stale metadata.
## Build and trust boundary

Run the lock check first: `uv build` does not accept `--locked` and build-system
requirements are separate from the application lock.

```sh
uv lock --check
uv build --no-sources --force-pep517 \
  --build-constraints build-constraints.txt --require-hashes
sha256sum dist/*
```

For offline builds, provision the reviewed backend wheel in a dedicated directory
and add `--no-index --find-links /path/to/reviewed-wheels`. For a wheel rebuilt
from an sdist, add `--wheel dist/scryntic-0.1.0.dev0.tar.gz` and a separate output
directory. Install the wheel in a fresh environment with `uv pip install`, then
check imports using that environment's `python -I` outside the source tree.

Locked synchronization can use the matching backend embedded in the pinned uv.
The explicit PEP 517 build above also exercises the separately hash-checked
backend distribution. uv/uv_build contain native executable code: pins and hashes
constrain reviewed artifacts, not upstream trust or runtime sandboxing. Build in
an unprivileged environment without application credentials. Inspect archive
members; internal documents, secrets and working data must not enter artifacts.
Dependency vulnerability gates are F02, not an assurance supplied by this baseline.

## F01 artifact inventory and validation

Reviewed on 2026-09-15. Python comes from Astral's
[python-build-standalone 20260901 artifact](https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz),
as selected and checksum-verified by pinned uv's
[download metadata](https://github.com/astral-sh/uv/blob/0.12.13/crates/uv-python/download-metadata.json).
The following PyPI wheels were downloaded over HTTPS and their SHA-256 values
checked before use; both packages declare `MIT OR Apache-2.0`.

| Input artifact | SHA-256 |
| --- | --- |
| CPython archive linked above | `72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c` |
| Installed `python3.12` executable | `f7c6210eb40fadcd3c2889dddd24a15fc2c9f926aec5a03bf9da66e12d581526` |
| [uv 0.12.13 Linux x86_64 wheel](https://files.pythonhosted.org/packages/0c/31/f774e18ea9282f63fb3a4f83db8b818b860fb10efda1d295e4547e993bf8/uv-0.12.13-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl) | `5312891392eb5b72eaca9b880e85fe3e706a2b11342e59f7a5cf0304d14d5cc5` |
| Extracted `uv` executable | `b59310db262709ee92baf7954ef30820f1442ffa48b263f7001a236fe9004047` |
| [uv_build 0.12.13 Linux x86_64 wheel](https://files.pythonhosted.org/packages/07/00/df9f20a3f82cbcb55fa11ad3bd56a8659bfbfcf7f8c395763917b2af4cf1/uv_build-0.12.13-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl) | `03121fd93aebf85a3a8ff2edcd1cd2b15a5f9eab1a3a51f3867ceb8487af3baa` |

Fresh virtual environments and a clean source copy passed locked, offline,
non-editable synchronization for base/collector/analysis and a second unchanged
sync. Each environment contained only `scryntic==0.1.0.dev0`. Wheel installation
and isolated imports passed outside the source tree; model packages were absent.
PEP 517 sdist/wheel builds passed with the backend hash enforced; rebuilding the
wheel from the sdist produced identical bytes. Archive contents were inspected:
the sdist includes the pins/locks, package, license, README and backend-generated
metadata (`PKG-INFO`, normalized `pyproject.toml`, original `pyproject.toml.orig`).
The wheel contains only the package and distribution metadata/license.

Negative checks in disposable copies rejected a changed project version with a
stale lock (without rewriting the lock), uv 0.12.3, and an incorrect backend hash.
No product tests, model execution or server deployment were performed.

| F01 output artifact | SHA-256 |
| --- | --- |
| `uv.lock` | `e0d6b2f62d84867eb37bfef4a6b884feb73cf52d5c9baa4c825473f3e3ecdeab` |
| `scryntic-0.1.0.dev0.tar.gz` | `9efc98d8b73f42b981359d13a5ec4d030138da73e70f794c58907b5e318e96b9` |
| `scryntic-0.1.0.dev0-py3-none-any.whl` | `06d003652f68d2bfd71fcb92b05edbe7ea4e04dd4183bfce4f7389cbf2f47cc9` |

This inventory describes the F01 candidate, not a published release. This document
is outside the sdist to avoid a circular artifact hash. Recompute output hashes
when package inputs change; dependency locks do not promise bit-identical future
native/GPU builds. No GPU or additional native runtime is required in F01.

Behavior was checked using Context7 and upstream documentation for
[locking/syncing](https://docs.astral.sh/uv/concepts/projects/sync/),
[build backends](https://docs.astral.sh/uv/concepts/build-backend/) and
[uv settings](https://docs.astral.sh/uv/reference/settings/).
