# Quality and supply-chain gates

Run these commands from a clean checkout with the F01 **uv 0.12.13** tool on
`PATH`. Python remains **CPython 3.12.14** from `.python-version`; the supported
range and **uv_build 0.12.13** pin are unchanged. Provisioning is an explicit
developer/operator step, never application startup behavior.

```sh
uv python install --no-bin
bash scripts/check.sh
uv run --locked --no-sync python scripts/check_negative.py
```

The development environment is `.venv`. GitHub Actions runs these same commands
on `ubuntu-24.04`; local qualification also covers Fedora 44 x86_64. The runner
image receives upstream updates, so this is reproducible dependency/tool
selection, not a bit-identical operating-system image.

## Gates

`scripts/check.sh` stops at the first failure:

1. `uv lock --check` rejects stale metadata; `uv sync --locked` installs only the
   explicitly selected development group. Neither command repairs the lock.
2. Ruff checks lint and formatting without changing files. Rules cover Python
   errors, imports, supported syntax and common bug patterns.
3. mypy checks owned source, tests and Python gate scripts in strict mode, with
   the `src` package base configured explicitly. No blanket missing-import or
   untyped-vendor exceptions are present.
4. pytest uses importlib discovery and strict configuration/marker validation.
   Tests cover the package and audit coverage/rejection behavior.
5. Packaging checks build both sdist and wheel through the hash-pinned PEP 517
   backend, rebuild a wheel from the sdist, and compare the wheels. Each of base,
   collector and analysis gets a fresh locked environment with no editable
   project. The built wheel is then installed without resolving dependencies.
   Isolated Python outside the source tree verifies metadata, license and every
   owned package file against independent source hashes. `py.typed` is the real
   PEP 561 resource marker; there is no invented application resource.
6. Audit checks every applicable package/version in the exported base, collector,
   analysis and dev closures, plus the build backend and provisioning uv.

Use `uv run --locked --no-sync python scripts/check_package.py` or
`uv run --locked --no-sync python scripts/audit.py` to repeat individual gates
after the development environment has been synchronized. Ruff/mypy/pytest can
also be run individually with the exact commands in `scripts/check.sh`.

`scripts/check_negative.py` runs entirely in temporary source copies. It verifies
nonzero failures for tests, lint, formatting, types and stale locked sync; it
builds a wheel deliberately missing `py.typed` and verifies rejection. A fake
unavailable audit service must also fail the audit command. The modified inputs
must remain unchanged by the checks. No broken fixtures remain in the checkout.

## Dependency review

All new tools and their transitive dependencies live in the `dev` group.
Collector/analysis/shared runtime requirements remain empty at F02. Installation
checks reject development tools in runtime environments and model libraries in
base/collector. Keep future optional model dependencies out of those profiles.

| Direct development requirement | Purpose | Declared license / origin |
| --- | --- | --- |
| pytest 9.1.1 | Tests and failure fixtures | MIT; `pytest-dev/pytest` |
| Ruff 0.16.7 | Lint and formatting checks | MIT; `astral-sh/ruff` |
| mypy 2.3.1 | Strict static checking | MIT; `python/mypy` |
| pip-audit 2.10.1 | Known-vulnerability queries | Apache-2.0; `pypa/pip-audit` |
| packaging 26.3 | Evaluate requirement markers and audit identities | Apache-2.0 OR BSD-2-Clause; `pypa/packaging` |

The reviewed lock uses only the public PyPI registry and SHA-256-addressed
`files.pythonhosted.org` artifacts. The Linux development closure contains 38
third-party distributions. Reviewed license metadata is MIT/BSD/Apache/PSF,
with MPL-2.0 for certifi and pathspec. Preserve applicable notices on redistribution;
these dependencies are not included in Scryntic's runtime wheel.
Ruff, mypy/librt/ast-serialize and some transitive dependencies include native
code. Tool/backend/installer execution is a provisioning trust boundary, not a
sandbox. Inspect new versions, origins, licenses, native components and build
hooks when reviewing lock changes. Lock hashes do not prove upstream code safe.

Audit exports retain the lock's hashes. `pip-audit --disable-pip` prevents pip
resolution/installation; hashed project/build exports use `--require-hashes`.
The separate exact uv version query uses `--no-deps`, without installing uv.
Reports must cover the expected package/version set exactly: unknown/skipped,
missing, duplicate or vulnerable results fail, as do tool/service errors. Empty
runtime closures are explicitly reported as zero third-party packages, rather
than pretending to query an unpublished `scryntic` distribution. The owned
package is covered by tests and code review.

Logs contain JSON audit evidence and build artifact hashes. The initial F02
qualification found no known vulnerabilities and required no suppressions or
dispositions. Audit data changes over time: unavailable data is not a clean
result. A future exception requires explicit scoped, owner-reviewed approval,
reason, affected version/vulnerability and expiry; no ignore-all or auto-fix
path is provided. This gate is not an audit of Python itself, model artifacts
or OS/GPU components.

## GitHub Actions trust

The workflow handles `push` and `pull_request` on GitHub-hosted Linux runners.
It grants only `contents: read`; unspecified token permissions are disabled.
Checkout does not persist credentials. No repository/environment secrets,
privileged PR events, self-hosted runners, external report uploads, shared caches,
auto-fixes or auto-merge are used. PR code is untrusted even when tests pass.

All external actions use full commit SHAs resolved from their upstream release
tags: checkout v7.0.1 and setup-uv v10.1.0. Both use Node 24 on the hosted runner.
setup-uv explicitly selects F01's uv version and disables cache persistence.
Review action code/version updates and the dependency diff separately from the
test result; passing checks do not grant merge approval. Maintainers configure
required PR checks in repository settings; this task does not change those settings.

Context7 and primary documentation informed this configuration:
[uv](https://docs.astral.sh/uv/concepts/projects/sync/),
[Ruff](https://docs.astral.sh/ruff/configuration/),
[pytest](https://docs.pytest.org/en/stable/explanation/goodpractices.html),
[mypy](https://mypy.readthedocs.io/en/stable/config_file.html),
[pip-audit](https://github.com/pypa/pip-audit),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use),
[setup-uv](https://github.com/astral-sh/setup-uv).
