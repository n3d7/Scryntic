# Quality and supply-chain gates

Run these commands from a clean checkout with the F01 **uv 0.12.13** tool on
`PATH`. Python remains **CPython 3.12.14** from `.python-version`; the supported
range and **uv_build 0.12.13** pin are unchanged. Provisioning is an explicit
developer/operator step, never application startup behavior.

F16 additionally requires operator-provisioned Bubblewrap/libseccomp and available
unprivileged Linux namespaces. `scripts/check_import_boundary.py` explicitly
qualifies the fixed parser profile; unavailable controls fail instead of skipping
tests. CI provisions these mechanisms on an ephemeral Ubuntu runner. The `analysis`
profile includes pinned PyArrow for restricted workstation decoding and AsyncSSH
2.24.0 for F17 read-only SFTP; the base
profile remains free of native archive decoders. See [IMPORTS.md](IMPORTS.md).

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
   explicitly selected dev, collector and analysis groups for the complete test
   suite. Neither command repairs the lock.
2. Ruff checks lint and formatting without changing files. Rules cover Python
   errors, imports, supported syntax and common bug patterns.
3. mypy checks owned source, tests and Python gate scripts in strict mode, with
   the `src` package base configured explicitly. No blanket missing-import or
   untyped-vendor exceptions are present.
4. pytest uses importlib discovery and strict configuration/marker validation.
   Coverage.py measures package and owned Python-script line/branch coverage and
   writes `coverage.xml` for SonarQube. Both `src/scryntic` and `scripts` are measured
   because the full Sonar configuration analyzes them. Unmeasured restricted-worker
   code remains uncovered; measurement
   never adds tracing hooks, writable paths or authority inside the worker.
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

## Full SonarQube review

Run `scripts/check.sh` before `sonar-scanner -Dsonar.qualitygate.wait=true` from
the repository root. Use the existing `sonar-project.properties`; provide server
credentials through the private operator environment, never repository files or
command-line tokens. Record the analyzed revision, branch, date and dirty-tree
state: local working-tree analysis is distinct from a published commit.

Read complete open issues, Security Hotspots, gate conditions, coverage details,
duplication blocks and project measures through the official read-only SonarQube
MCP. Review source and tests for each attributable finding, then repair, validate,
regenerate coverage and repeat the full analysis. A passing gate is not evidence
that all findings were reviewed. Conversely, a verified API false positive or
necessary control-flow construct must have a concrete recorded rationale; do not
change thresholds, severities, exclusions or suppressions to hide it. Server issue
dispositions require a separately authorized review mechanism.

The [2026-10-03 full review](sonarqube/full-review-2026-10-03.md) records the
replacement server's authoritative snapshot and subsequent repairs.
The [2026-10-04 follow-up](sonarqube/closure-2026-10-04.md) records revalidation,
individually authorized issue dispositions and the current zero-open-issue result;
it distinguishes source repairs from Accepted and False Positive records.

`scripts/check_negative.py` runs entirely in temporary source copies. It verifies
nonzero failures for tests, lint, formatting, types and stale locked sync; it
builds a wheel deliberately missing `py.typed` and verifies rejection. A fake
unavailable audit service must also fail the audit command. The modified inputs
must remain unchanged by the checks. No broken fixtures remain in the checkout.
The stale-lock control uses a fresh temporary uv cache and permits registry
access: uv resolves changed metadata before refusing the lock update. It must
reach the lockfile diagnostic; a network or resolution failure is not a pass.

## Dependency review

All new tools and their transitive dependencies live in the `dev` group.
Collector/analysis/shared runtime requirements remain empty at F02. Installation
checks reject development tools in runtime environments and model libraries in
base/collector. Keep future optional model dependencies out of those profiles.

F17 adds AsyncSSH **2.24.0** only to `analysis`; packaging rejects it in base
and collector installs. Its declared license is `EPL-2.0 OR GPL-2.0-or-later`;
redistribution uses the EPL-2.0 option and retains its notices. The reviewed
PyPI closure adds cryptography **50.0.2** (`Apache-2.0 OR BSD-3-Clause`),
cffi **2.1.1** (`MIT-0`) and pycparser **3.0** (`BSD-3-Clause`). Cryptography
and cffi contain native code. These libraries implement workstation transport,
never the restricted archive decoder or collector role. Pin/hash/audit checks
do not establish SSH or filesystem isolation. The adapter's bounded SFTP reader
depends on the inspected 2.24.0 factory API; upgrades require its protocol tests.

| Direct development requirement | Purpose | Declared license / origin |
| --- | --- | --- |
| pytest 9.1.1 | Tests and failure fixtures | MIT; `pytest-dev/pytest` |
| coverage.py 7.16.2 | Line/branch measurement and SonarQube XML | Apache-2.0; `coveragepy/coveragepy` |
| Ruff 0.16.7 | Lint and formatting checks | MIT; `astral-sh/ruff` |
| mypy 2.3.1 | Strict static checking | MIT; `python/mypy` |
| pip-audit 2.10.1 | Known-vulnerability queries | Apache-2.0; `pypa/pip-audit` |
| packaging 26.3 | Evaluate requirement markers and audit identities | Apache-2.0 OR BSD-2-Clause; `pypa/packaging` |

The reviewed lock uses only the public PyPI registry and SHA-256-addressed
`files.pythonhosted.org` artifacts. The development closure is exported and audited
from the current lock. Reviewed license metadata is MIT/BSD/Apache/PSF,
with MPL-2.0 for certifi and pathspec. Preserve applicable notices on redistribution;
these dependencies are not included in Scryntic's runtime wheel.
Coverage.py, Ruff, mypy/librt/ast-serialize and some transitive dependencies include native
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
