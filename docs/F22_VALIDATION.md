# F22 / KER-26 validation — 2026-10-09

**Status: implementation available, qualification incomplete. Draft PR / In
Progress; not In Review or Done.** Base main:
`62f9aee731dbac3eadcac03422413e329174498f`.
Branch: `ker-26-f22-timesfm`. The approved architecture and F19/F20/F21 contracts
were inspected. F21 is merged/Done in the tracker, but its 15 mandatory host
probes remain unperformed, as explicitly confirmed by the operator.

## Implementation and available boundary evidence

| Requirement | Verified portable evidence | Mandatory remaining evidence |
| --- | --- | --- |
| Interchangeable provider selection | Closed TOML selects TimesFM/fake persistence/fake trend; identical F20 jobs, artifacts and F19 comparison services; reruns execute distinct jobs in the same installation | Actual isolated executions of selected TimesFM and fake on the qualification host |
| Offline model/runtime admission | Fixed model revision/three hashes, separate locked 25-package CPU closure, explicit inventory approval, bounded no-follow snapshots; drift/link/concurrent-mutation rejection | Successful real-runtime launch and resource measurements |
| CPU isolation | Original synthetic profile preserved; separate candidate CPU profile; real subprocess seccomp tests allow bounded pthreads and deny fork/exec/network | Effective identity/namespaces/mounts/cgroups/limits and every F21/F22 host probe |
| Credentials and results | Exact environment, no inherited descriptors, immutable request/model/runtime mounts required; existing F21 safe bounded result import; malformed control/resource/result rejection | Host sentinel environment/FD denial, write denial, isolated output-link failures |
| Hindsight-free evaluation | F19 observable contiguous contexts, gaps/quality exclusion, temporal label purging and identical Decimal metrics; empty input refuses launch before creating jobs | TimesFM offline forecasts, two independent executions per case, immutable accepted artifacts and baseline comparison |
| Cleanup/failure/restart | Snapshot cancellation joins before deleting staging; real subprocess repeated cancellation and F20 fencing remain tested | Actual unit cancellation/stop/restart, exhaustion and abrupt coordinator death |

Unit adapter/provider tests use controlled doubles and establish contract
compatibility, not successful PyTorch inference or host containment. No GPU
profile or financial/exchange authority was introduced. See
[F22_MODELS.md](F22_MODELS.md) for pins, exact loading/preprocessing, CPU bounds,
kernel/runtime/provisioning trust assumptions and residual limitations.

On this Fedora 44 x86-64 host: Linux `7.2.7-200.fc44.x86_64`, systemd
`259.9-1.fc44`, glibc `2.43-8.fc44`, libseccomp `2.6.1-2.fc44`, CPython 3.12.14.
These are observed inputs, **not a qualified host profile**.

## Provisioning and real Bybit baseline

The fixed TimesFM revision and all three artifact hashes in F22_MODELS.md were
downloaded/verified again on October 9, outside the worker. Runtime enrollment
remained `66228d61bd3f01d44c836cf7b1a99ade417bce5dd52240d95d7b82d6dd6c96ec`:
16,674 files / 835,983,738 bytes. Recomputing the installed package closure and
every admitted file hash reproduced that inventory exactly. This is this
installation's approved inventory, not a portable hash to approve blindly.
Models, venvs, raw data and private installation state are ignored/uncommitted.

After the pause, system uv was 0.12.19, which the exact pin correctly rejected.
A separate official uv 0.12.18 Linux archive was verified against its upstream
checksum before extracting its executable. Archive SHA-256:
`89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16`.
No global tooling, root lock, build-backend pin or gate changed.

```sh
.venv/bin/python -m scripts.prepare_f22_bybit \
  --root "$PWD/state/f22-bybit-oct09" --code-revision ker-26-f22-timesfm
```

Public Bybit BTCUSDT spot 1-minute capture used F15 durable repair, F16 import,
F18 provenance and strict finalized/coverage/time-quality exclusions. It accepted
192 rows, with actual complete coverage evidence. The healthy recovery sample
had offset -296,687 ns, uncertainty 77,071,738 ns and age 99,108,865,488 ns;
the final sample was stale/unknown. No clock limits were widened and no receipt
or coverage evidence fabricated. Replay uses the retained row/coverage evidence,
not the final sample as a substitute. Historical reconstruction is explicit.

Dataset manifest SHA-256:
`ed6096e18e2b55e9fc64919dbac11821dbc18f5cd9a71e7ab410ea9219d97e5c`.
Baseline report SHA-256:
`8147e847764ce95dbe7d7ed610869389bdb038a5c33faf01b15e5fc1779ce3d8`.
Full F19 baseline: 188 scored / 4 excluded (three label-purged rows and one
outside/crossing split). Values below are rounded for readability; canonical
reports retain exact Decimal arithmetic.

| Partition | Full baseline count | Full MAE | Full RMSE | Same-case F22 baseline count | Same-case MAE | Same-case RMSE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 94 | 44.5266 | 52.9977 | 2 | 19.65 | 23.6441 |
| Validation | 47 | 27.2894 | 36.2783 | 2 | 19.40 | 20.9093 |
| Test | 47 | 23.3340 | 28.7406 | 2 | 33.65 | 40.7311 |

The verified F22 plan selected rows `[1, 2, 95, 96, 143, 144]` with contexts
`[2, 3, 96, 97, 144, 145]`. Row 0 lacks two observable contiguous closes.
Actual model metrics, predictions, accepted model artifacts and measured model
resource use are **pending**, not zero or inferred from mocks. Unknown pretraining
overlap and this small sample preclude a model-training holdout/general quality
claim even after execution. The model need not beat persistence.

## Commands and scanner results

Normal gates and focused tests use the reviewed uv 0.12.18 executable:

```sh
PATH=/tmp/f22-tools/bin:$PATH bash scripts/check.sh
PATH=/tmp/f22-tools/bin:$PATH uv run --locked --no-sync python scripts/check_negative.py
.venv/bin/python -m pytest tests/models -q
```

The final October 9 normal gate exited **0**: **1675 passed / 31 host tests
skipped** in 95.34 seconds, strict typing in 273 files, lint/format, both lock checks,
packaging/installed profiles, and all seven complete pip-audit closures. All
seven disposable negative controls rejected unchanged inputs. An independent
read-only review also passed 60 focused boundary tests; it found and verified the
rerun job-identity repair. Tests dependent
on real ownership/native boundaries were run outside Codex's synthetic
nobody-owned filesystem namespace; that is not system-manager authorization.

SonarScanner CLI 8.0.1 full analysis used the existing local Community Build
server/private credentials and unchanged configuration. It stores this local
working-tree snapshot on the `Scryntic` main component, **not remote main or PR
analysis**. Final analysis on 2026-10-09, after the full portable suite:
new coverage **80.7%** (required 80%), new duplication **0.0%** (maximum 3%),
six open new-period violations (required zero), therefore gate **ERROR**.
No findings were suppressed or server dispositions changed.

Six source-reviewed findings remain open:

| Rule / key | Review against implementation |
| --- | --- |
| S7497 / f000854a-71df-433b-90f0-55255bbbbaaf | New snapshot cancellation waits for its thread, observes failure and propagates caller cancellation; repeated cancellation/failure tests protect staging cleanup. |
| S2612 / 3b84e4a2-e70f-48c8-9463-a4850d9e6387 | `0444` applies to verified private copies needed by DynamicUser under read-only mounts. Source ancestors are pinned; staging ancestor is private. Host mount qualification is pending. |
| S5443 / c9b92aaa-a683-4fa9-86be-64c6d27a2129 | `/tmp` is a fixed expected mount name for private bounded tmpfs, not public host-file creation. |
| S7497 / e9ffdfd3-6c7e-46f5-9f7e-52f46b396338 | F21 shielded cleanup joins repeated cancellation before propagation. |
| S7497 / e0bf360c-0659-47d2-92d5-c35e7f3e815a | F21 cancellation during spawn joins spawn, cleans the actual unit and propagates cancellation. |
| S8997 / b08463be-4c60-4dff-b777-053e79ed12f2 | Existing test literally uses `monkeypatch.setattr(sys, "path", sys.path.copy())`; pytest restores the original list. |

These are proposed reviewer dispositions, not a green quality gate. Genuine
host-only uncovered behavior remains uncovered; no tracing authority or gate
exceptions were added. Attributable complexity/exception-scope defects found
during development were repaired and rescanned. No hotspots awaited review.

Semgrep 1.179.0 ran 151 local official Python security rules over 29 affected
production/script files: **0 findings / 0 errors**, including every new models
module. CE lacks Pro inter-file analysis and commercial SCA/Secrets.

Trivy 0.75.0 `fs --scanners vuln,misconfig,secret --include-dev-deps` scanned the
root and forecast CPU locks: **0 CVEs / secrets / misconfigurations**; no supported
IaC files were detected. DB updated `2026-10-09T13:10:00Z`, downloaded
`2026-10-09T17:08:22Z`. JSON conversion produced an 82-component CycloneDX SBOM.
OS/kernel/driver inventory and manually provisioned tools are not lockfile CVE
coverage. The separate pip-audit closure includes the provisioning uv pin.

PyPI advisory lookup cannot resolve local `torch==2.14.0+cpu`. The auditor maps
only the exact reviewed CPU version/wheel hash to public `torch==2.14.0`, while
retaining all original locked identities in evidence and checking every
dependency. The official CPU source commit
`08187d9e0fba026dc8217405802ab5381dc88d90` differs from official v2.14.0
`2b3ec34829036a65cd9d1398ea72a0167dc37470` only by a test import, verified via
[upstream comparison](https://github.com/pytorch/pytorch/compare/2b3ec34829036a65cd9d1398ea72a0167dc37470...08187d9e0fba026dc8217405802ab5381dc88d90).
Unknown versions/hashes fail closed; no CVE is ignored.

## Exact operator boundary and manual qualification

The fixed real-runtime positive test was attempted with verified artifacts and
failed closed. The prerequisite command was separately attempted:

```sh
/usr/bin/systemd-run --system --no-ask-password --wait --collect \
  --unit=scryntic-f22-permission-probe /usr/bin/true
```

It returned access denied: interactive authentication is required but disabled.
No sudo, host installation/polkit change, weaker isolation or unisolated model
run was used by Codex. All 15 F21 plus 16 F22 host probes remain mandatory.

On an operator-authorized disposable/reviewed Linux host, provision the locked
runtime/artifacts and approved TOML according to F22_MODELS.md. From this reviewed
checkout, use an account already authorized for fixed system-manager units.
If the operator explicitly chooses manual root qualification, the following
sudo invocations are manual actions, not an elevated launcher shipped in Scryntic:

```sh
sudo -- env SCRYNTIC_F21_HOST=1 "$PWD/.venv/bin/python" -m pytest -s \
  -o cache_dir=/tmp/scryntic-f21-qualification-cache tests/model_worker/test_host.py
sudo -- env SCRYNTIC_F22_HOST=1 SCRYNTIC_F22_CONFIG=/absolute/reviewed-model.toml \
  "$PWD/.venv/bin/python" -m pytest -s \
  -o cache_dir=/tmp/scryntic-f22-qualification-cache tests/models/test_host.py
```

Require all 31 host tests pass, none skipped. Retain full logs, printed effective
controls/resource JSON, exact code/model/runtime hashes, kernel/systemd/glibc/
libseccomp/CPython identity, unit cleanup/restart and bounded abrupt-death
evidence. Review/remove only identified stale staging after the death probe;
do not call its mere existence cleanup success. A different host qualifies only
that host. Any missing control/failing probe requires repair and rerun.

Create a **fresh** qualification dataset installation under the same UID that
will evaluate it. Do not reuse user-owned private catalog state under root:

```sh
sudo -- mkdir -m 0700 /var/tmp/scryntic-f22-qualification
sudo -- "$PWD/.venv/bin/python" -m scripts.prepare_f22_bybit \
  --root /var/tmp/scryntic-f22-qualification/bybit --code-revision "$(git rev-parse HEAD)"
sudo -- "$PWD/.venv/bin/python" -m scripts.check_f22_model \
  --dataset-root /var/tmp/scryntic-f22-qualification/bybit \
  --configuration /absolute/reviewed-model.toml \
  --output /var/tmp/scryntic-f22-qualification/timesfm.json \
  --code-revision "$(git rev-parse HEAD)"
```

This needs real healthy F12 clock evidence during capture; stale/uncertain data
must remain excluded. Then run the **same** evaluation command with a TOML
containing only `[model] selected = "fake-persistence"` and a new `--output` path.
Retain both reports and pinned dataset/publications/objects plus F20 accepted
forecast artifacts. Require identical comparison rows/labels/splits/exclusions,
finite bounded outputs, independent repeat equality, recorded effective
controls/resource use and model-vs-persistence metrics on the same cases.
Repeat normal gates/scans after any repair. Only after this evidence and the
unchanged quality gate are resolved may KER-26 move to **In Review**. Do not merge
the PR or mark Done automatically.
