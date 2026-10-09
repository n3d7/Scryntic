# F22 / KER-26 validation — 2026-10-10

**Status: implementation merged, qualification incomplete. KER-26 is In
Progress; not In Review or Done.** PR #26 was merged by the operator; the main
merge revision used for the first qualification attempt is
`4be4ea2c3ee171f5d76e72ad6bbe00c62b4523f1`. Original implementation base:
`62f9aee731dbac3eadcac03422413e329174498f`.
Original branch: `ker-26-f22-timesfm`; current candidate repair branch:
`ker-26-f22-host-mount-repair`. The approved architecture and F19/F20/F21
contracts were inspected. F21 is merged/Done in the tracker, but its host
qualification was missing at implementation delivery. The first operator
attempt below failed; neither profile is qualified yet.

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

## Commands and scanner results before the mount repair

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

### Follow-up verification on merged main

The clean F22 worktree was fast-forwarded to merged main without source changes.
The unrelated dirty F21 checkout was preserved. On October 9, safe verification
on that revision produced:

- `.venv/bin/python -m pytest tests/models tests/model_worker -q`: **157 passed,
  31 host skipped**, exit 0; log `/tmp/f22-qualification-portable.log`.
- All three admitted model artifacts verified; recomputing the complete installed
  runtime inventory reproduced
  `66228d61bd3f01d44c836cf7b1a99ade417bce5dd52240d95d7b82d6dd6c96ec`.
- F16/F18 verified the retained Bybit objects/publications/manifest. F19 reproduced
  the original canonical baseline twice byte-for-byte, including the recorded
  historical baseline environment. A relocated, quiescent private copy also
  reproduced it twice. Same-case rows/contexts/metrics remain those above.
- The actual UID is 1000; system-manager is running. Cgroup v2 exposes CPU,
  memory and pids controllers, Bubblewrap is `0.12.0-1.fc44`, and approximately
  29 GiB memory / 24 GiB `/tmp` space were available. These prerequisites do not
  establish effective controls.
- `pkcheck --action-id org.freedesktop.systemd1.manage-units --process "$$"`,
  without `--allow-user-interaction`, exited **2** (not authorized). This agrees
  with the installed upstream `pkcheck(1)` manual. No unit or root action was
  attempted in this follow-up.
- Read-only SonarQube queries still report the six reviewed findings and gate
  `ERROR` above. No disposition, rule, exclusion or gate was changed.

Local, ignored evidence is under `state/f22-qualification`: `verified-bybit.json`,
`bybit-copy-verification.json`, the verified `bybit-copy`, and a reviewed local
`verify_bybit.py` helper. The helper checks bounded JSON, fixed dataset/baseline
hashes, active F16/F18 pins, the unchanged lock and two exact F19 reproductions.
It does not load a model or launch a unit. The model-specific execution/resource
fields remain explicitly pending authorization. The source has not changed, so
the full gates/scans above and successful PR CI remain prior revision-equivalent
evidence; they were not rerun as though host qualification had occurred.

### First operator attempt and candidate mount repair

The operator ran all 31 tests on October 9 at 19:09 UTC, on the recorded Fedora
host and merged revision `4be4ea2c3ee171f5d76e72ad6bbe00c62b4523f1`. Prerequisites
exited 0; all model/runtime/lock hashes matched. The returned JUnit records
**25 passed / 6 failed / 0 skipped / 0 errors**, 41.828 seconds, host exit 1.
The failures are both profiles' positive execution, cancellation/restart and
abrupt-death tests. Evaluation exited 1 at its host-status guard; no dataset
copy or model evaluation reports were created. There is no new model/resource
success evidence. Returned private files are in
`state/f22-qualification/operator-results`; preserve this first attempt.

The accessible unit journal identifies `226/NAMESPACE`: systemd could not
create the missing destination inode at `/input/request` (permission denied),
then the bind mount failed with ENOENT before bootstrap execution. The launcher
prepared the input directory but omitted the file mount point. The systemd 259
[bind-mount contract](https://github.com/systemd/systemd/blob/v259/man/systemd.exec.xml)
and [namespace implementation](https://github.com/systemd/systemd/blob/v259/src/core/namespace.c)
require an existing destination or permission to create it. Context7 confirmed
the upstream mount preparation behavior; two targeted SOFA searches yielded no
directly applicable systemd evidence, so no community workaround was adopted.

The candidate repair pre-creates an empty read-only regular file inside the
coordinator's private staging root, before the existing fixed read-only request
bind. Namespace, identity, mount, credential, network and resource properties
remain unchanged. The focused regression fails against the original launcher
and requires the file/type/permissions/private ancestor to exist before launch.
Portable tests are not host proof: all 31 probes must be repeated against the
reviewed repair revision. The 25 initial negative-test passes are not credited
as scenario qualification, because a common startup failure could satisfy their
expected failure. Model comparison remains blocked until both positive profiles
and the entire host suite pass. KER-26 remains In Progress.

Candidate validation on October 9, before committing the reviewed source:

- Focused `tests/models tests/model_worker`: **158 passed / 31 host skipped**,
  exit 0; regression first failed against the original launcher.
- `PATH=/tmp/f22-tools/bin:$PATH bash scripts/check.sh`: **1676 passed / 31
  host skipped** in 96.80 seconds, exit 0, including typing, lint/format, locks,
  installed/package checks and all seven dependency-audit closures.
- `PATH=/tmp/f22-tools/bin:$PATH uv run --locked --no-sync python
  scripts/check_negative.py`: all seven negative controls rejected their defects;
  inputs remained unchanged, exit 0.
- Semgrep affected production file `src/scryntic/model_worker/launcher.py`:
  **0 findings / 0 errors**. Trivy root/runtime locks, configuration and secrets:
  **0 CVEs / misconfigurations / secrets**. Both commands exited 0; rules and
  exclusions remained unchanged.
- Full Sonar analysis at **19:30 UTC** used the same local `Scryntic` main
  component and unchanged quality policy: coverage **80.8%**, duplication
  **0.0%**, six previously recorded open violations, gate **ERROR** (scanner
  exit 3). New test finding S9073 / `dc6c49ea-2cdd-4d66-aaa7-89d98643004f`
  was repaired by splitting assertions; the rescan confirmed it **CLOSED**.
  None of the six earlier findings were suppressed or dispositioned.

Local command logs/scanner JSON are retained under ignored
`state/f22-qualification/mount-repair-validation/`. This portable evidence
does not qualify either worker profile or prove the repair works on the host.
All 31 actual host probes and the offline Bybit model comparison remain pending.

### Second operator attempt: executable access denied

The operator repeated the suite on `dedf6798a76d73d9b950dabbce34d825526d2753`
at 19:38 UTC on October 9. Returned evidence is in
`state/f22-qualification/operator-results-scryntic-f22-qualification-20261009T193744Z`.
JUnit confirms **31 tests / 25 passed / 6 failed / 0 skipped / 0 errors**,
42.312 seconds, exit 1. Prerequisites exited 0 and enrollment/artifact/lock
hashes remained unchanged. The same six positive/lifecycle tests failed.

The scoped journal records **30 executable lookup failures, 203/EXEC**:
`/python/bin/python3.12` is denied before bootstrap runs. The previous missing
request-mount diagnostic is absent. No actual TimesFM forecasts or effective
worker-control evidence were produced. The negative passes are still not
scenario qualification. Units-after is empty; staging-before and staging-after
contain the same two pre-existing directories, not evidence of newly leaked
staging from this run. They were not deleted.

Read-only inspection as the real UID 1000 outside Codex's synthetic namespace
found SELinux **Enforcing**, CPython executable and immediate directories mode
0755, executable label `unconfined_u:object_r:data_home_t:s0`, and no `noexec`
on the containing host filesystem. Sandbox-reported SELinux/mount state does
not represent this host. SELinux denial is a hypothesis, not a confirmed cause:
the ordinary UID cannot read `/var/log/audit` (root-owned 0700), and scoped
journald queries returned no AVC. systemd v259
[executable lookup source](https://github.com/systemd/systemd/blob/v259/src/core/exec-invoke.c)
places this error after namespace setup. Context7 supplied upstream context;
current source and actual audit evidence govern any subsequent repair.

Before another worker run or source/policy change, the operator must collect
the retained AVCs for the first synthetic and real-profile failing processes.
The installed upstream `ausearch(8)` documents PID/time/type filtering. These
commands only read the existing audit records; no worker/unit is launched:

```bash
F22_AUDIT_RETURN="/home/void/Project/Scryntic/.worktrees/ker26/state/f22-qualification/operator-results-scryntic-f22-qualification-20261009T193744Z"
F22_AUDIT_DATE="$(LC_ALL=C TZ=UTC date --date=2026-10-09 +%x)"
for F22_PID in 1503865 1504775; do
  sudo -- env TZ=UTC LC_ALL=C /usr/bin/ausearch \
    --message AVC,USER_AVC \
    --start "$F22_AUDIT_DATE" 19:38:37 --end "$F22_AUDIT_DATE" 19:39:21 \
    --pid "$F22_PID" --interpret \
    > "$F22_AUDIT_RETURN/avc-$F22_PID.txt" 2>&1
  F22_AUDIT_STATUS=$?
  printf 'ausearch exit status: %s\n' "$F22_AUDIT_STATUS" \
    >> "$F22_AUDIT_RETURN/avc-$F22_PID.txt"
  cat "$F22_AUDIT_RETURN/avc-$F22_PID.txt"
done
```

Required evidence is the denied operation and `scontext`, `tcontext`, `tclass`,
PID/path and enforcing/permissive indicator, or an explicit no-match/error
result. The original four-digit-year date was rejected by this host's C locale;
the corrected `%x` date (`10/09/26`) passed an unprivileged parser check against
an empty audit input. This check is not actual AVC evidence. No match does not
clear SELinux or prove a DAC failure. Do not disable
SELinux, add broad allow rules/capabilities, change labels or make private
ancestors traversable as a diagnostic workaround. The exact remaining boundary
is privileged audit-log access; implementation changes await this evidence.
No gates were rerun for this documentation-only update, no source change or new
commit/PR was made, and the six open Sonar findings remain unresolved.

### Confirmed SELinux cause and exact executable-label repair

Returned `avc-1503865.txt` and `avc-1504775.txt` both record enforcing
(`permissive=0`) denials of file `execute`: subject `init_t`, target
`data_home_t`, interpreter `python3.12`, inode 2897660. Audit queries exited 0.
This confirms the executable-label cause for both profiles; it does not qualify
any subsequent runtime behavior.

The operator requested a SELinux adjustment. This host's `matchpathcon
/usr/bin/python3` reports `bin_t`. The scoped repair is one persistent regular-file
context for the exact approved CPython executable, then `restorecon` for that
file only. No new allow rule, domain exception, permissive mode, recursive home
relabel or capability is introduced. The systemd mount/identity/network/resource
contract is unchanged. SHA-256 of the inspected interpreter is
`f7c6210eb40fadcd3c2889dddd24a15fc2c9f926aec5a03bf9da66e12d581526`.
It is an installation identity, not a substitute for upstream runtime provenance.
The interpreter remains operator-user-owned trusted code; this label is not an
integrity guarantee against that user, a host administrator or source replacement.

[Red Hat's labeling guidance](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/using_selinux/troubleshooting-problems-related-to-selinux_using-selinux)
and the installed `semanage-fcontext(8)` require persistent context configuration
plus label application; the latter requires precise regexes to avoid unrelated
matches. Context7 had no directly applicable SELinux entry, and a targeted SOFA
search found no applicable second opinion. No community workaround was adopted.
`sudo -n -l` requires a password, so Codex has not applied a host change.

Run manually on this installation in the existing Bash session. The exact regex
escapes dots and selects only a regular file. If adding the record fails (for
example, an existing customization), stop and return the error rather than
overwriting another rule. Before/after hashes must match; require `Enforcing`,
`bin_t`, successful `matchpathcon -V` and unchanged 0755 mode:

```bash
set +u
set -o pipefail
F22_PY='/home/void/.local/share/uv/python/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12'
F22_PY_CONTEXT='/home/void/\.local/share/uv/python/cpython-3\.12\.14-linux-x86_64-gnu/bin/python3\.12'
(
  set -e -o pipefail
  test ! -L "$F22_PY"
  test "$(getenforce)" = Enforcing
  printf '%s  %s\n' \
    f7c6210eb40fadcd3c2889dddd24a15fc2c9f926aec5a03bf9da66e12d581526 \
    "$F22_PY" | sha256sum --check
  ls -lZ "$F22_PY"
  sudo -- /usr/bin/semanage fcontext -a -f f -t bin_t "$F22_PY_CONTEXT"
  sudo -- /usr/bin/restorecon -v "$F22_PY"
  /usr/bin/matchpathcon -V "$F22_PY"
  ls -lZ "$F22_PY"
  printf '%s  %s\n' \
    f7c6210eb40fadcd3c2889dddd24a15fc2c9f926aec5a03bf9da66e12d581526 \
    "$F22_PY" | sha256sum --check
  getenforce
)
```

Return the outcome before another run. If successful, first rerun only the two
positive host tests with fresh JUnit/journal evidence, then all 31 mandatory
probes. More denials require examination of their actual AVCs; do not expand the
label scope automatically. Runtime libraries, staging and application labels
have not been qualified by this single executable change. The comparison stays
blocked. To undo only this newly added record and restore the default type:

```bash
sudo -- /usr/bin/semanage fcontext -d -f f "$F22_PY_CONTEXT"
sudo -- /usr/bin/restorecon -v "$F22_PY"
```

The operator applied the exact file-context adjustment and returned successful
before/after hash checks, `bin_t`, unchanged 0755 mode and `Enforcing`. Read-only
inspection by Codex outside the synthetic namespace independently reproduced
the same hash/label/mode, successful `matchpathcon -V` and `Enforcing`.
No allow module, other label change or successful worker execution is claimed.
Source remains `dedf6798a76d73d9b950dabbce34d825526d2753`; only validation
documentation is locally modified. No source checks/scanners were repeated.

The next manual step is two positive probes, using a fresh evidence directory
and the same reviewed runtime/model configuration. This diagnostic pair cannot
replace the full 31-test qualification or actual Bybit comparison:

```bash
set +u
set -o pipefail
cd /home/void/Project/Scryntic/.worktrees/ker26 || exit 1
F22_REPO="$PWD"
F22_REV=dedf6798a76d73d9b950dabbce34d825526d2753
test "$(git rev-parse HEAD)" = "$F22_REV" || exit 1
git diff --exit-code HEAD -- src tests scripts runtimes pyproject.toml uv.lock || exit 1
F22_POSITIVE_ROOT="/var/tmp/scryntic-f22-positive-$(date -u +%Y%m%dT%H%M%SZ)"
sudo -- mkdir -m 0700 "$F22_POSITIVE_ROOT" || exit 1
sudo -- find /tmp -maxdepth 1 -type d -name 'scryntic-model-*' -print \
  | sudo -- tee "$F22_POSITIVE_ROOT/staging-before.txt"
F22_POSITIVE_STARTED="$(date -u --iso-8601=seconds)"
sudo -- env SCRYNTIC_F21_HOST=1 SCRYNTIC_F22_HOST=1 \
  SCRYNTIC_F22_CONFIG="$F22_REPO/state/f22-timesfm.toml" \
  "$F22_REPO/.venv/bin/python" -m pytest -v -s \
  -o addopts=--import-mode=importlib \
  -o "cache_dir=$F22_POSITIVE_ROOT/pytest-cache" \
  --junitxml="$F22_POSITIVE_ROOT/positive.xml" \
  tests/model_worker/test_host.py::test_effective_host_controls_and_f20_result \
  tests/models/test_host.py::test_real_runtime_offline_inference_and_effective_controls \
  2>&1 | sudo -- tee "$F22_POSITIVE_ROOT/positive.log"
F22_POSITIVE_STATUS=$?
F22_POSITIVE_FINISHED="$(date -u --iso-8601=seconds)"
printf 'Positive probe exit status: %s\n' "$F22_POSITIVE_STATUS" \
  | sudo -- tee "$F22_POSITIVE_ROOT/positive-status.txt"
sudo -- journalctl --utc --no-pager --since "$F22_POSITIVE_STARTED" \
  --until "$F22_POSITIVE_FINISHED" --unit='scryntic-model-*' \
  | sudo -- tee "$F22_POSITIVE_ROOT/worker-journal.log"
/usr/bin/systemctl --system --no-ask-password list-units --all \
  'scryntic-model-*' --no-pager \
  | sudo -- tee "$F22_POSITIVE_ROOT/units-after.txt"
sudo -- find /tmp -maxdepth 1 -type d -name 'scryntic-model-*' -print \
  | sudo -- tee "$F22_POSITIVE_ROOT/staging-after.txt"
F22_POSITIVE_RETURN="$F22_REPO/state/f22-qualification/operator-results-$(basename "$F22_POSITIVE_ROOT")"
test ! -e "$F22_POSITIVE_RETURN" || exit 1
sudo -- cp -a -- "$F22_POSITIVE_ROOT" "$F22_POSITIVE_RETURN" || exit 1
sudo -- chown -hR -- "$(id -u):$(id -g)" "$F22_POSITIVE_RETURN" || exit 1
printf 'Results ready: %s\n' "$F22_POSITIVE_RETURN"
```

Require both tests passed, zero skipped/failures/errors, status 0, actual
effective-control evidence and two equal native offline TimesFM executions.
Return logs/JUnit/journal after any outcome; inspect fresh AVCs for further
denials. Full qualification and evaluation remain blocked pending review.

### Pause checkpoint — 2026-10-09 20:00 UTC

After the exact interpreter relabel, the operator ran the diagnostic pair on
`dedf6798a76d73d9b950dabbce34d825526d2753`. Returned JUnit independently confirms
**2 failed / 0 passed / 0 skipped / 0 errors**, 2.516 seconds, exit 1. Evidence:
`state/f22-qualification/operator-results-scryntic-f22-positive-20261009T195959Z`.
The retained journal now says **Failed to execute** `/python/bin/python3.12`,
`203/EXEC: Permission denied`, PID **1630894** at 19:59:59 UTC. This differs from
the earlier executable lookup denial; no cause for the new execution failure
has been established. The journal export ends at second precision and contains
only the synthetic unit; do not infer the real unit's failure stage from it.
Units-after is empty and staging-before/after contain the same two pre-existing
directories. No successful worker/control/model/resource evidence was produced.

The operator requested stopping until tomorrow. Work is paused, not completed.
Do not start another qualification run, alter more labels or rules, or begin
F23. The next step after explicit resumption is the retained new AVC for PID
1630894 in **2026-10-09 19:59:58–20:00:10 UTC**, plus a complete journal interval
for the real-profile attempt. Use the corrected C-locale date `10/09/26`; retain
actual denied permission and subject/target contexts before choosing any repair.
The existing `bin_t` adjustment remains applied; it did not qualify execution.
All 31 host probes, offline same-case Bybit evaluation/reproducibility/resource
evidence and six Sonar dispositions remain incomplete. KER-26 stays In Progress.
No new source change, allow module, gate exception, push or PR was introduced.

### Resumed: loader symlink AVC and candidate directory-bind repair

On October 10 the operator returned the retained audit record for PID 1630894:
`audit(10/09/26 19:59:59.427:2367)`, denied **read**, `name=lib64`,
`scontext=system_u:system_r:init_t:s0`,
`tcontext=unconfined_u:object_r:user_tmp_t:s0`, `tclass=lnk_file`,
`permissive=0`; ausearch status 0. The journal query returned no entries,
status 0. Evidence is in the latest returned positive directory's
`avc-1630894.txt` and `worker-journal-complete.txt`. This identifies the
synthetic execution failure; it does not establish the real-profile failure
stage or successful worker execution. Ordinary-UID host inspection confirmed
Enforcing and the existing bin_t executable label. No host policy was changed.

The launcher constructed `lib -> usr/lib` and `lib64 -> usr/lib64` beneath its
private temporary root. The actual interpreter's ELF program-interpreter path
is `/lib64/ld-linux-x86-64.so.2`. SELinux controls the symlink inode separately
from its target: see the [SELinux project's object-class reference](https://github.com/SELinuxProject/selinux-notebook/blob/main/src/object_classes_permissions.md).
The repair creates ordinary `lib`/`lib64` mount-point directories and binds
the same selected `/usr/lib` and, when present, `/usr/lib64` read-only at both
locations. It exposes no additional host source, writable library mount,
credentials or user configuration. The versioned [systemd 259 mount contract](https://github.com/systemd/systemd/blob/v259/man/systemd.exec.xml)
supports distinct host-source/service-root-destination binds; Context7's
systemd documentation agrees. A targeted SOFA search returned no applicable
SELinux/systemd symlink evidence; unrelated mount posts were not relied on.

Regression checks cover both owned CPU profiles, hosts with/without
`/usr/lib64`, absence of writable library binds, directory rather than symlink
targets and the existing private staging/request contract. The four mount
specification cases fail against the previous committed `_command`; all five
focused checks pass for the candidate. Portable checks do not establish that
SELinux permits the completed launch. The next operator action is the two
positive host probes with a fresh evidence directory and complete live journal
export; only after they succeed should all 31 mandatory probes be repeated.
Further denied operations require actual AVC evidence before another repair.

Candidate validation (ordinary UID 1000; no root/system-manager launch):

- `PATH=/tmp/f22-tools/bin:$PATH bash scripts/check.sh`: status 0; **1,680
  passed / 31 mandatory host skips**, 96.04 seconds. Both lock checks, lint,
  formatting, strict typing (273 files), packaging/installed profiles and all
  dependency audits pass. The restored official uv 0.12.18 archive matches the
  SHA-256 recorded above; the global uv installation and pins are unchanged.
- `PATH=/tmp/f22-tools/bin:$PATH uv run --locked --no-sync python
  scripts/check_negative.py`: status 0; all seven negative controls reject
  their deliberately invalid inputs and preserve the originals.
- Semgrep local Python security snapshot: 151 rules, both affected Python
  files, **0 findings / 0 errors**; CLI status 0.
- Trivy `fs --scanners vuln,misconfig,secret --include-dev-deps --format json`:
  status 0, both root/forecast-runtime locks detected, **0 vulnerability/secret
  findings**. No supported IaC files were detected; the systemd property
  contract was reviewed/tested directly, not claimed as Trivy IaC coverage.
- Full local SonarQube `Scryntic` default/main analysis completed **2026-10-09
  22:29 UTC** on this worktree candidate: coverage **80.9%**, duplication **0%**,
  unchanged gate **ERROR**, scanner status 3. The same **six existing open
  issue keys** remain (S7497 x3, S2612, S5443, S8997); no new issue was introduced
  by this repair. Findings are still unresolved, not silently accepted or
  excluded. Server default/main is a local candidate snapshot, not proof of
  remote main/PR validation.

Actual logs/JSON are ignored under
`state/f22-qualification/loader-alias-validation/`. Codex sandbox execution
stalled in the threaded portable test; only those session-owned pytest processes
were stopped. The five focused checks and full gates then ran successfully
outside that sandbox as the ordinary UID. This is portable validation, not
successful execution across the model-worker isolation boundary. No root
operation, policy weakening, host-probe pass, model metric, resource measurement,
new PR or push is claimed. All mandatory qualification and Sonar closure remain
blocked; KER-26 stays In Progress and F23 is not started.

### Operator prerequisites and stage 1: all 31 probes

The commands below are for this reviewed host and retained enrollment. Another
host requires independent provisioning/inventory approval according to
[F22_MODELS.md](F22_MODELS.md), and qualifies only that host. Review the tests,
coordinator and local helper before executing them with root authority. Stop all
other Scryntic model jobs/coordinators first; cleanup assertions inspect the fixed
worker-unit prefix. Preserve the existing source/dataset while copying it.
Use a fresh qualification directory; do not reuse an earlier run or disable a
failed control. Budget ample `/tmp` space for verified per-job snapshots and the
configured CPU/memory limits. This is manual qualification, not a generic
elevated launcher or permission change shipped in Scryntic.

Run in a Bash terminal (the pipelines retain command failures). Leave interactive
`nounset` disabled: Fedora's prompt can expand unset `PROMPT_*` variables. If a
previous attempt enabled it, run `set +u` first; these shell settings do not change
the worker profile. Use a fresh qualification directory for each run:

```bash
set +u
set -o pipefail
cd /home/void/Project/Scryntic/.worktrees/ker26
F22_REPO="$PWD"
F22_REV="$(git rev-parse HEAD)"  # reviewed candidate commit supplied in the handoff
F22_QROOT="/var/tmp/scryntic-f22-qualification-$(date -u +%Y%m%dT%H%M%SZ)"
test "$(git rev-parse HEAD)" = "$F22_REV" || exit 1
git diff --exit-code HEAD -- src tests scripts runtimes pyproject.toml uv.lock || exit 1
test -f "$F22_REPO/state/f22-timesfm.toml" || exit 1
sudo -- mkdir -m 0700 "$F22_QROOT" || exit 1
(
  set -e -o pipefail
  {
  date -u --iso-8601=seconds
  git rev-parse HEAD
  id
  sudo -- id
  uname -a
  rpm -q systemd glibc libseccomp bubblewrap
  "$F22_REPO/.venv/bin/python" --version
  stat -fc %T /sys/fs/cgroup
  cat /sys/fs/cgroup/cgroup.controllers
  df -h /tmp "$F22_REPO"
  sha256sum uv.lock runtimes/forecast_cpu/uv.lock \
    runtimes/forecast_cpu/.venv/inventory.json state/f22-timesfm.toml \
    state/f22-qualification/verify_bybit.py \
    models/f22/approved/model.safetensors models/f22/approved/config.json \
    models/f22/approved/README.md
  } 2>&1 | sudo -- tee "$F22_QROOT/prerequisites.log"
)
F22_PREREQ_STATUS=$?
printf 'Prerequisites exit status: %s\n' "$F22_PREREQ_STATUS" \
  | sudo -- tee "$F22_QROOT/prerequisites-status.txt"
test "$F22_PREREQ_STATUS" -eq 0 || exit 1
sudo -- find /tmp -maxdepth 1 -type d -name 'scryntic-model-*' -print \
  | sudo -- tee "$F22_QROOT/staging-before.txt"
F22_HOST_STARTED="$(date -u --iso-8601=seconds)"
sudo -- env SCRYNTIC_F21_HOST=1 SCRYNTIC_F22_HOST=1 \
  SCRYNTIC_F22_CONFIG="$F22_REPO/state/f22-timesfm.toml" \
  "$F22_REPO/.venv/bin/python" -m pytest -v -s \
  -o addopts=--import-mode=importlib \
  -o "cache_dir=$F22_QROOT/pytest-cache" \
  --junitxml="$F22_QROOT/host.xml" \
  tests/model_worker/test_host.py tests/models/test_host.py \
  2>&1 | sudo -- tee "$F22_QROOT/host.log"
F22_HOST_STATUS=$?
F22_HOST_FINISHED="$(date -u --iso-8601=seconds)"
sudo -- journalctl --utc --no-pager --since "$F22_HOST_STARTED" \
  --until "$F22_HOST_FINISHED" --unit='scryntic-model-*' \
  | sudo -- tee "$F22_QROOT/worker-journal.log"
/usr/bin/systemctl --system --no-ask-password list-units --all \
  'scryntic-model-*' --no-pager \
  | sudo -- tee "$F22_QROOT/units-after.txt"
sudo -- find /tmp -maxdepth 1 -type d -name 'scryntic-model-*' -print \
  | sudo -- tee "$F22_QROOT/staging-after.txt"
printf 'Host pipeline exit status: %s\n' "$F22_HOST_STATUS" \
  | sudo -- tee "$F22_QROOT/host-status.txt"
```

Require **31 passed, zero skipped/errors/failures and status 0**. Retain full logs
and JUnit, printed effective-control/resource JSON, denied host files/sockets/
network, immutable inputs, credentials/descriptors, symlink/hardlink/flood,
process/thread/memory/CPU exhaustion, missing-control rejection, cancellation,
restart and bounded abrupt-death evidence. The real-runtime positive probe must
perform two equal offline native TimesFM executions with 512 context rows and
horizon 24. A passing synthetic profile cannot replace it. Compare staging
before/after and review/remove only newly identified stale staging after the
death probe. Its existence is a documented coordinator-death limitation, not
proof of cleanup. Do not remove unrelated paths or grant broader unit authority.
If any prerequisite/probe fails, stop before stage 2 and return that evidence.

### Stage 2: offline evaluation of the same retained Bybit cases

Only after stage 1 passes, make a **new root-owned copy** of the already verified,
quiescent installation; do not recapture a different market sample and do not
open user-owned private catalogs under root. Ownership changes below apply only
to that new copy. Keep the Bash variables/cwd from stage 1:

```bash
(
  set -e -o pipefail
  test "$F22_HOST_STATUS" -eq 0
  sudo -- test ! -e "$F22_QROOT/bybit"
  sudo -- cp -a -- "$F22_REPO/state/f22-qualification/bybit-copy" "$F22_QROOT/bybit"
  sudo -- chown -hR -- root:root "$F22_QROOT/bybit"
  sudo -- "$F22_REPO/.venv/bin/python" \
    "$F22_REPO/state/f22-qualification/verify_bybit.py" "$F22_QROOT/bybit" \
    2>&1 | sudo -- tee "$F22_QROOT/dataset-verification.json"
  sudo -- "$F22_REPO/.venv/bin/python" -m scripts.check_f22_model \
    --dataset-root "$F22_QROOT/bybit" \
    --configuration "$F22_REPO/state/f22-timesfm.toml" \
    --output "$F22_QROOT/timesfm.json" --code-revision "$F22_REV" --per-partition 2 \
    2>&1 | sudo -- tee "$F22_QROOT/timesfm.log"
  sudo -- "$F22_REPO/.venv/bin/python" -m scripts.check_f22_model \
    --dataset-root "$F22_QROOT/bybit" \
    --configuration "$F22_REPO/examples/local-model.toml" \
    --output "$F22_QROOT/fake.json" --code-revision "$F22_REV" --per-partition 2 \
    2>&1 | sudo -- tee "$F22_QROOT/fake.log"
  sudo -- "$F22_REPO/.venv/bin/python" -m scripts.check_f22_model \
    --dataset-root "$F22_QROOT/bybit" \
    --configuration "$F22_REPO/state/f22-timesfm.toml" \
    --output "$F22_QROOT/timesfm-repeat.json" --code-revision "$F22_REV" --per-partition 2 \
    2>&1 | sudo -- tee "$F22_QROOT/timesfm-repeat.log"
)
F22_EVAL_STATUS=$?
printf 'Evaluation exit status: %s\n' "$F22_EVAL_STATUS" \
  | sudo -- tee "$F22_QROOT/evaluation-status.txt"
```

The scripts execute the fixed offline worker, not an unisolated vendor import.
Every report must contain six same-case predictions and two distinct F20 jobs
per case (12 accepted artifacts); the repeat report uses fresh qualification/job
identities. Require the manifest/baseline hashes and rows/contexts above,
identical labels/splits/exclusions/provenance and baseline metrics in all three
reports, finite bounded points, equal TimesFM predictions/metrics across reruns,
valid accepted artifact hashes/sizes/identities and effective controls. Expect
fake persistence to match the same-case F19 persistence baseline. Record measured
per-job elapsed time, CPU time and maximum RSS from `resource_use`; configured
limits are not measurements. Artifact/job identities and resource measurements
may differ between equal prediction reruns. A failed job/report is a blocker,
not a zero metric. Preserve the copied dataset, catalogs and accepted artifacts
under `bybit/home/.local/state/scryntic/forecasts` for independent validation.
No runtime downloads, remote code, exchange credentials or execution authority
are authorized. This small historical sample and unknown pretraining overlap
retain the limitations in F22_MODELS.md; no absolute containment claim follows.

### Return evidence and await review

After either a failed stage 1 or completion of stage 2, export a **new copy** of
the evidence for the ordinary operator UID. Leave the root qualification source
intact. The returned directory must not already exist:

```bash
F22_RETURN="$F22_REPO/state/f22-qualification/operator-results-$(basename "$F22_QROOT")"
test ! -e "$F22_RETURN" || exit 1
sudo -- cp -a -- "$F22_QROOT" "$F22_RETURN" || exit 1
sudo -- chown -hR -- "$(id -u):$(id -g)" "$F22_RETURN" || exit 1
```

Return the directory path and terminal exit status; Codex must inspect the logs,
31 JUnit outcomes, actual reports and artifacts before recording qualification.
The copied installation is evidence, not an instruction to run new root jobs
from user-owned catalogs. The six Sonar findings need an authorized review of
the stated source evidence and any host evidence relevant to mount/cancellation
behavior; proposed dispositions are not resolved findings. The configured MCP
is read-only. Validate any actual defect before repair; repeat relevant host
probes and normal gates/scans after source changes. Only when all mandatory
evidence and the unchanged quality gate are resolved may KER-26 move to **In
Review**. Do not start F23, create a documentation-only PR or mark Done.
