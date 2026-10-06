# F21 / KER-25 validation — 2026-10-06

**Status: implementation pending host qualification, not completed/In Review.**
Base main: `3fdebbe3d19d55f0225240d3cb0056528239141a`.
Branch: `ker-25-f21-model-isolation`. F16/KER-20 and F20/KER-24 were Done in
Linear and their implementation/contracts were inspected on this main.
Pre-existing changes in QUALITY.md, RECOVERY.md, readme.md and the untracked
F01_F20_REVIEW.md are excluded from this delivery.

## Boundary evidence

| Criterion | Available evidence | Remaining qualification |
| --- | --- | --- |
| Fixed CPU spec, missing controls reject | Fixed properties/no shell/path fragments; every required evidence field/member rejection tested; missing manager prevents subprocess creation; missing effective controls prevents provider invocation | Actual DynamicUser/namespaces/cgroups and missing-property negative launches |
| Denied host files/sockets/network/devices | Mandatory bootstrap probes implemented; no host home/config/SSH/control-socket input mounts | Privileged actual-host probes have not passed |
| Immutable input | Read-only generated request and runtime binds, required mount evidence and attempted write | Privileged write-denial probe pending |
| Result link/race/bounds | Real filesystem tests reject symlinks, hardlinks, FIFO, directory, oversize and replacement during read; pinned parent survives pathname replacement; canonical F20 validation independently preserved | Actual isolated output tmpfs/link probes pending |
| Environment/secrets/FDs | Empty coordinator subprocess environment; manager-name stripping; exact worker allowlist and no extra FD requirements; sanitized failures tested | Host sentinel environment and inheritable descriptor probe pending |
| Process/memory/CPU | Required cgroup/rlimit fields; mandatory fork denial and over-limit allocation; CPU/flood failure probes implemented | Actual constrained-host exhaustion probes pending |
| Cancellation/restart | Real subprocess repeated cancellation during spawn joins cleanup; pipe floods/nonzero/malformed output fail; F20 acceptance still owns durable state | Actual unit stop, restart and abrupt coordinator death/runtime-expiry probes pending |

Actual attempted prerequisite command, outside the Codex sandbox:

```sh
/usr/bin/systemd-run --system --no-ask-password --quiet --wait --collect \
  --property=DynamicUser=yes --property=PrivatePIDs=yes /usr/bin/true
```

It failed with **access denied: operation requires interactive authentication**.
The actual fixed-launcher positive host test was also attempted with
`SCRYNTIC_F21_HOST=1` and failed closed. No sudo/install/polkit changes or weaker
rootless substitute were made. The rootless Bubblewrap identity probe showed
UID 65534 mapped to host UID 1000; it is not separate host-identity evidence.

Observed host inventory, not a qualified-profile claim: Fedora 44 x86-64,
Linux `7.2.7-200.fc44.x86_64`, CPython 3.12.14, systemd 259.9-1.fc44,
libseccomp 2.6.1-2.fc44, glibc 2.43-8.fc44, Bubblewrap 0.12.0-1.fc44.
See [MODEL_WORKERS.md](MODEL_WORKERS.md) for the TCB, effective-control contract,
no-GPU decision and residual limitations, including staging after SIGKILL.

## Commands and checks

The initial qualification used hash-verified uv 0.12.13. Both PR/push CI runs
passed all tests and packaging but failed its provisioning-tool audit. The user
approved a narrow repair: the exact uv CLI pin in `pyproject.toml` and the CI
setup step now use **0.12.18**. The independently pinned `uv_build==0.12.13`,
its reviewed build wheel/hash, Python pin and runtime lockfile are unchanged.
No audit exception, scanner rule, exclusion, threshold or security control
was weakened.

The new Linux x86_64 provisioning artifact was downloaded over HTTPS into `/tmp`
and its wheel SHA-256 checked against
[PyPI release metadata](https://pypi.org/pypi/uv/0.12.18/json) before execution.
This inventory supersedes the previous F01 uv CLI artifact only:

| Input artifact | SHA-256 |
| --- | --- |
| [uv 0.12.18 Linux x86_64 wheel](https://files.pythonhosted.org/packages/67/67/def11543e7bd3b4219ca29afd66f3accab641324bd2e426cb788083c56c5/uv-0.12.18-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl) | `fbe0489871e74ebfb70379a32526c62b9fb615acf13576b9f19bc09a142f62b0` |
| Extracted `uv` executable | `b97ae0e1ed3661fcd0c446cc59d81f5720c24d35627b21814a7581fb67eba673` |

```sh
PATH=/tmp/f21-repair-bin:$PATH bash scripts/check.sh
PATH=/tmp/f21-repair-bin:$PATH uv run --locked --no-sync python scripts/check_negative.py
.venv/bin/python -m pytest tests/model_worker tests/jobs tests/imports/test_launcher.py
```

Focused final snapshot: **167 passed, 15 privileged host tests skipped**. Portable
tests and mocks are not isolation qualification. The full gates passed locked
sync, lint/format, strict typing, tests and packaging/installed-profile checks;
the repaired-tooling suite snapshot was **1578 passed, 15 host tests skipped**
in 94.66 s. `scripts/check.sh` exited **0** with uv 0.12.18: locked sync,
lint/format, strict typing, packaging/installed profiles and all six complete
pip-audit closures (base, collector, analysis, dev, build and provisioning uv)
passed. The runtime lock and build-backend pin/hash did not change.
All seven disposable negative gate
controls passed (test, lint, format, type, lock, missing resource, unavailable audit).

The initial normal gate failed at provisioning uv's audit:
`CVE-2026-104843` / `GHSA-2cv4-cqwr-gwf7`, fixed in uv 0.12.18. The
[upstream advisory](https://github.com/astral-sh/uv/security/advisories/GHSA-2cv4-cqwr-gwf7)
states that only Windows hosts are affected. This Linux implementation is not an
affected wheel-installation environment. The strict gate remains platform
independent: the approved uv update addresses the advisory without an exception.
The complete gate passed with the reviewed new CLI, including provisioning uv's
report with an empty vulnerability list. The initial failed CI runs were
`37454035353` (push) and `37454157027` (PR), both with the same audit cause.

## Scanner evidence and disposition

Full SonarScanner CLI 8.0.1 analysis used existing private credentials and unchanged
sonar-project.properties, followed by read-only MCP issue/hotspot/coverage review.
This Community Build server records local working-tree analyses on its `main`
component; it is not a remote-main publication or PR analysis. Analysis included
pre-existing dirty documentation; all new attributable findings were inspected.

Seventeen initial findings were repaired: fixed policy path/result constants,
explicit busy-loop behavior, safe final bootstrap exit, dictionary comprehension,
ASCII-preserving regex, test exception scopes and assertions. Four attributable
findings remain **open**, with no server disposition or suppression:

| Rule / key | Source-grounded review |
| --- | --- |
| S5443 / 9205d06c-1821-47ec-9707-023a9184b1f3 | WRITABLE_MOUNTS contains `/tmp` as an expected mount name, not creation of a temporary file in a public host directory. Worker storage is private tmpfs; coordinator staging uses TemporaryDirectory's private ancestor. Actual mount qualification is still pending. |
| S7497 / e9ffdfd3-6c7e-46f5-9f7e-52f46b396338 | Shielded cleanup catches repeated cancellation until bounded cleanup finishes, then propagates CancelledError; it does not resume provider work. Removing the join would orphan cleanup and violate F21. |
| S7497 / e0bf360c-0659-47d2-92d5-c35e7f3e815a | Cancellation during spawn is retained while the spawn task is joined, followed by shielded cleanup and propagation of the original cancellation. Real subprocess repeated-cancel tests exercise this path. |
| S8997 / b08463be-4c60-4dff-b777-053e79ed12f2 | The flagged line is literally `monkeypatch.setattr(sys, "path", sys.path.copy())`; pytest's fixture restores the original list. The test does not manually assign global state. Keeping a copy prevents bootstrap path mutation from leaking into other tests. |

These are proposed reviewer dispositions, not authorization to change the server.
The zero-new-violations gate therefore remains **ERROR**. Restricted-worker code
has genuine uncovered lines; no tracing hooks or extra authority were added to
inflate coverage. No Security Hotspots awaited review in the inspected snapshot.
The final code-commit analysis (`d692d70794f924ed13f10a1a290c1188d4c94cd6`)
was recorded on the Community Build `main` component at
`2026-10-06T11:02:37Z`: gate ERROR, new coverage **85.8%** (threshold 80%),
new duplication **0.0%** (threshold 3%), four new violations (threshold zero).
This is the F21 branch's local code snapshot, not published main. Coverage.py
recorded 84.53% line and 70.61% branch coverage globally. Synthetic preflight
rejection covers part of bootstrap; privileged boundary execution remains a gap.

Semgrep 1.179.0: local official Python security snapshot, metrics/version checks
off, explicit model_worker/import bootstrap/test paths, **0 findings / 0 errors**.
CE does not establish Pro inter-file data-flow or dependency/secret coverage.

Trivy 0.75.0: `fs --scanners vuln,misconfig,secret --format json`, unchanged local
configuration, **0 findings** for current uv.lock/selected repository files.
The scan also encountered two existing worktree lockfiles; they were not edited.
DB updated `2026-10-06T07:04:23Z`, downloaded `2026-10-06T10:39:55Z`.
The configuration/tooling repair was rescanned with the same Trivy command;
dependency vulnerabilities, misconfigurations and secrets remained **0**.
Trivy does not inventory the custom provisioning uv pin as a lock dependency,
which explains the separate pip-audit finding. This repository scan is not an
OS/kernel/driver CVE inventory. There are no new Python dependencies or GPU inputs.
Raw local scanner reports/logs are under `/tmp/f21-*`, not committed as payload dumps.

## Required manual qualification and next step

On an operator-authorized, reviewed Linux host with systemd 257+, delegated
cgroup v2 memory/pids/cpu controls and libseccomp, from the reviewed checkout:

```sh
sudo -- env SCRYNTIC_F21_HOST=1 \
  /home/void/Project/Scryntic/.venv/bin/python -m pytest -s \
  -o cache_dir=/tmp/scryntic-f21-qualification-cache tests/model_worker/test_host.py
```

Use the exact checkout interpreter path if the host differs. This is a manual
operator action, not a privileged helper shipped with Scryntic. Retain the printed
`f21-cpu-v1` effective-control JSON and full results. Require **all host tests pass,
none skipped**; failures need repair and a rerun, not weakened controls. Record
kernel/systemd/libseccomp/runtime inventory and stale-staging cleanup after the
abrupt-death probe. A successful test on a different host qualifies only that host.

Until host qualification and the unchanged gates are resolved, the PR stays draft
and KER-25 stays In Progress. Only fully validated work may move to In Review;
Done and merging remain reviewer actions.
