# F22 / KER-26 validation — 2026-10-10

**Status: mandatory operator qualification and local repair gates passed;
repair delivery is ready for review.** The tracker should move to In Review
with the repair PR and remain there until operator review; do not mark Done
automatically. PR #26 was merged by the operator; the main
merge revision used for the first qualification attempt is
`4be4ea2c3ee171f5d76e72ad6bbe00c62b4523f1`. Original implementation base:
`62f9aee731dbac3eadcac03422413e329174498f`.
Original branch: `ker-26-f22-timesfm`; current candidate repair branch:
`ker-26-f22-host-mount-repair`. The approved architecture and F19/F20/F21
contracts were inspected. F21 is merged/Done in the tracker, but its host
qualification was missing at implementation delivery. The first operator
attempt below failed. Candidate `c8a920d` subsequently passed all 31 mandatory
host tests, the relocated root decoder preflight and actual Bybit evaluation
on this installation. The later artifact-metadata repair is committed as
`f0f1e3eebc297ba424160c8d7ce39b32d51fd91d`; the original operator evidence
remains bound to `c8a920d`. A source diff verifies that worker, model, runtime,
decoder, qualification scripts and host tests are unchanged between these
revisions. Metadata serialization is separately covered by the new regressions
and the full portable gates. This is combined evidence, not a claim that all
host probes were repeated on the later commit.

The final full Sonar analysis at `2026-10-10T15:40Z` passed after the user
authorized all seven individually reviewed FALSE_POSITIVE dispositions and
their comments. Gate OK: new coverage 81.4%, duplication 0%, new violations 0;
live read-only queries confirm zero OPEN/CONFIRMED issues and zero TO_REVIEW
hotspots. Policy, thresholds and exclusions are unchanged. Detailed rationale:
[F22 Sonar review](sonarqube/f22-qualification-review-2026-10-10.md).

Older dated checkpoints below preserve failure history. Their pending/blocked
statements and superseded commands are historical; this final checkpoint takes
precedence. F23 has not started and requires a separate user command.

## Actual Bybit evaluation checkpoint — 2026-10-10

The operator ran TimesFM, fake persistence and a fresh TimesFM repeat using the
verified relocated coordinator at source revision `c8a920d`, beginning
`2026-10-10T11:59:52Z`. Evaluation exited 0. Returned evidence is
`state/f22-qualification/operator-results-evaluation-relocated-20261010T115948Z/`.
Report SHA-256 values independently verified against their canonical bytes:

| Report | SHA-256 |
| --- | --- |
| timesfm.json | `56f5c405c938dbb087e636f03c0115f59ebfc85b442fe201b6835e5fde39bd7b` |
| fake.json | `1def3795069a159fc80972609ba9c702484d22dbc4cda489001523942a2ec618` |
| timesfm-repeat.json | `b0768a6d62cd47a5ef68d4af7608ca5cd74bba1da41fca175c9c6293dbf6ae3c` |

`verify_operator_evaluation.py` independently read all accepted artifacts through
the production immutable store and all jobs through a read-only SQLite connection.
Its output `evaluation-independent-verification.json` records 36 unique durable
succeeded jobs and 36 unique accepted artifacts, exact effective controls for all
36 executions, identical data/provenance/exclusions/baseline, two executions per
case and equal TimesFM predictions across both reports. There are six retained
cases, two per partition. Fake persistence exactly matches F19's baseline.
Actual admitted seed 0 is present in every durable request and artifact's nested
determinism record. Artifact result identities/digests/request bindings and points
match the reports; forecast timestamps represent candle starts and their label
close times are one frequency step later. No native parser ran in this verifier.

| Partition | TimesFM MAE | Persistence MAE | TimesFM RMSE (rounded) | Persistence RMSE (rounded) |
| --- | ---: | ---: | ---: | ---: |
| train | 18.59140625 | 19.65 | 24.12581678 | 23.64413246 |
| validation | 13.4984375 | 19.4 | 15.36082815 | 20.90932806 |
| test | 33.29375 | 33.65 | 40.27308114 | 40.73113060 |

The train RMSE is worse despite a lower MAE. These six historical cases establish
successful offline execution and the comparison contract; they do not establish
general forecasting advantage, training holdout independence or live trading
suitability. Pretraining overlap remains unknown.

Across 24 native TimesFM executions, CPU time ranged from 4.212832 to 4.356927
seconds and measured inference elapsed time from 4.161005147 to 4.290524164
seconds. Reported `ru_maxrss` ranged from 2,136,012 to 2,137,132 KiB (about
2.04 GiB). Fake resource use is null, so no measured fake/native resource ratio
is claimed. Systemd's separately rounded 1G cgroup memory peak is a different
accounting observation; its discrepancy with `ru_maxrss` is unresolved.

Before/after unit and staging inventories are all empty. Four evaluation logs
were checked for known credential/FD/host-canary sentinels and none were found.
The captured journal contains 35 successful deactivations; its whole-second
`--until` boundary truncates the last worker's tail. All 36 durable successes
and artifacts plus the empty after-unit inventory independently establish
completion; this is not a claim of 36 complete journal exit records.

### Artifact-metadata correction after the operator checkpoint

Historical `c8a920d` artifacts retain top-level seed null and fake-provider
hardware wording, and nested limitations still describe an F20 fake fixture.
The correct admitted seed/device/runtime are independently present in the nested
record and durable requests, but the legacy descriptions are inaccurate for a
real model. These immutable evidence files have not been rewritten.

The subsequent focused serialization repair makes top-level seed/device match
the admitted job and uses provider-neutral reproducibility limitations. It
does not change artifact shape/schema, predictions, worker execution, admission,
dataset decoding or isolation controls. Regressions first failed on all three
provider selections and a nonzero seed; after repair, 39 focused tests passed.
The full `scripts/check.sh` run passed 1,789 tests, 31 opt-in host skips, both lock
checks, Ruff, strict mypy, packaging and dependency audits (exit 0). Evidence:
`state/f22-qualification/metadata-validation/`. This portable validation is
separate from actual `c8a920d` host/model evidence.

All seven negative controls passed after the metadata repair. The new full
Sonar analysis at `2026-10-10T15:30Z` exited 3: new coverage 81.4% and new
duplication 0% pass, while seven open violations keep the gate ERROR. It analyzed
the dirty source/test diff with SHA-256
`4abf5642a419e04fd4489c7e99788ee30a2e0dc95eff775e8b45f9ba6320dc9a`;
this is a local candidate analysis, not a published revision/PR gate.
That failed analysis was superseded by the successful full analysis at
`2026-10-10T15:40Z`, after explicit user approval of the seven individual server
dispositions. Their source/test rationales and applied statuses are in
[the F22 Sonar review](sonarqube/f22-qualification-review-2026-10-10.md).
No blanket suppression or quality-policy weakening was used. Final repair
publication/review is separate from these passed qualification gates.

## Implementation and available boundary evidence

| Requirement | Verified portable evidence | Mandatory remaining evidence |
| --- | --- | --- |
| Interchangeable provider selection | Closed TOML selects TimesFM/fake persistence/fake trend; actual TimesFM, fake persistence and fresh TimesFM repeat passed through identical F20/F19 services on the qualification host | Final repair publication and quality closure |
| Offline model/runtime admission | Fixed model revision/three hashes, separate locked 25-package CPU closure, explicit inventory approval, bounded no-follow snapshots; actual offline Bybit inference and 24 native resource measurements passed | Preserve revision-specific evidence |
| CPU isolation | Both fixed profiles passed all 31 mandatory host probes; all 36 Bybit jobs recorded and independently checked effective controls | No isolation changes in the metadata repair |
| Credentials and results | Host sentinel/FD/write/link probes passed; 36 accepted immutable artifacts and durable successes independently verified; four evaluation logs checked for sentinels | Historical artifacts retain the metadata limitation described below |
| Hindsight-free evaluation | Same six eligible Bybit cases, provenance, exclusions and baseline across three reports; two independent jobs per case, equal TimesFM points in repeat, persistence agrees with F19 | Small-sample and pretraining-overlap limitations remain |
| Cleanup/failure/restart | Snapshot cancellation joins before deleting staging; real subprocess repeated cancellation and F20 fencing; actual unit cancellation/stop/restart, exhaustion and bounded abrupt-death probes passed; two stale roots reviewed/removed by operator | Automatic host snapshot cleanup after coordinator SIGKILL remains unsupported; retain the documented limitation |

Unit adapter/provider tests use controlled doubles and establish contract
compatibility, not successful PyTorch inference or host containment. No GPU
profile or financial/exchange authority was introduced. See
[F22_MODELS.md](F22_MODELS.md) for pins, exact loading/preprocessing, CPU bounds,
kernel/runtime/provisioning trust assumptions and residual limitations.

On this Fedora 44 x86-64 host: Linux `7.2.7-200.fc44.x86_64`, systemd
`259.9-1.fc44`, glibc `2.43-8.fc44`, libseccomp `2.6.1-2.fc44`, CPython 3.12.14.
These are the observed inputs for the host-probe checkpoint below, not a claim
of absolute containment or completed model/dataset qualification.

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

#### Operator positive pair on the directory-bind candidate

The operator ran both positive probes on `fa857c0747b98acaf09282962e8749c22e3044c4`.
Returned JUnit independently confirms **2 failed / 0 passed / 0 skipped /
0 errors**, 2.877 seconds, pipeline status 1. Evidence:
`state/f22-qualification/operator-results-scryntic-f22-positive-20261009T224414Z`.
The complete live journal records **Started** for both units at 22:44:16 and
22:44:19 UTC, followed by `status=1/FAILURE`; the previous `203/EXEC` failure
is absent in this attempt. This establishes successful process execution, not
successful trusted preflight or inference. The failing runtime stage is unknown:
bootstrap's unconditional os._exit suppresses exception diagnostics and the
coordinator does not publish bounded worker stderr. No raw diagnostic/secret
output was enabled. Units-after is empty, and before/after staging lists match
the same two pre-existing paths (`a7x8wijv`, `2nmqle25`). No new stale path was
reported in this attempt. Effective-control/model/resource evidence remains
missing. Next action is the retained AVC/USER_AVC records for `python3.12` in
2026-10-09 22:44:14–22:44:22 UTC; absence of a matching AVC must be recorded and
would require safe runtime-stage diagnostics rather than a speculative policy
change. The full 31-probe suite and real evaluation remain blocked.

#### No matching runtime AVC; fixed bootstrap-stage diagnostics

The operator's constrained `python3.12` AVC/USER_AVC query returned **no
matches**, status 1, retained as `runtime-avc.txt` in that evidence directory.
This does not establish absence of every SELinux denial or identify the failing
Python/runtime operation. No new policy/label adjustment was made.

The trusted bootstrap now retains a fixed stage number and exits nonzero with
that number on a Python exception, including BaseException/SystemExit paths.
It prints no diagnostic text, traceback, exception value, input or credentials;
the coordinator still discards bounded stderr and raises its generic isolation
failure. Successful completion remains status 0; invalid failure-stage state
maps to 78, never success. This changes failure diagnostics only, not control
checks, result envelopes/import, registered models or F20 admission/durability.
Context7's CPython documentation agrees with the [official Python 3.12 os._exit
contract](https://docs.python.org/3.12/library/os.html#os._exit): this immediate
exit does not flush stdio or execute interpreter cleanup. The existing worker
cleanup choice is preserved, with exceptions still suppressed at process exit.

| Exit status | Fixed bootstrap stage |
| --- | --- |
| 70 | Application imports |
| 71 | Request/model definition decoding |
| 72 | Installing seccomp/privilege controls |
| 73 | Effective-control inspection and boundary probes |
| 74 | Fixed probe operation / attempt decoding |
| 75 | Provider inference |
| 76 | Result encoding and safe bounded import |
| 77 | Final envelope/resource serialization and pipe write |
| 78 | Invalid/unknown failure-stage state |

These codes are hints, not attestation or proof of a passed control. Failure
before the Python entrypoint, explicit crash probes and native signals can still
produce other statuses. A model could falsify diagnostics; successful evidence
must still pass all existing validation. Real subprocess tests inject sentinel
exception values and invalid stage values and require empty stdout/stderr and
the fixed nonzero statuses. The controls failure test verifies inference is
never called and the failing stage remains 73; portable tests also cover silent
successful exit and BaseException paths. No actual host-stage result is claimed
until the operator repeats the positive pair on the reviewed diagnostic commit.

Diagnostic candidate validation (no root job execution):

- Seven real-process sentinel/stage regression cases fail before entrypoint
  instrumentation and pass afterward, with empty stdout/stderr. Full normal
  `scripts/check.sh` using the reviewed uv 0.12.18: status 0; **1,690 passed /
  31 host skips**, 95.31 seconds; lock/lint/format/mypy/package and all dependency
  audits pass. The three entrypoint success/BaseException/invalid-stage tests
  and controls-before-inference regression also pass in this suite.
- All seven `scripts/check_negative.py` controls pass, status 0.
- Semgrep: 151 local Python rules / three boundary/test files, no findings or
  errors, status 0. Trivy root/forecast locks including dev and source secret
  scan: no findings, status 0; no supported IaC files detected.
- Full local SonarQube `Scryntic` default/main candidate snapshot completed
  **2026-10-09 22:57 UTC**: coverage **81.0%**, duplication **0%**, gate **ERROR**,
  scanner status 3. The same six previously recorded issue keys remain open;
  no new issue, exclusion or disposition was introduced.

Actual logs/JSON are ignored in `state/f22-qualification/stage-diagnostic-validation/`.
Host qualification, actual offline TimesFM/Bybit comparison, reproducibility,
resource/artifact evidence and Sonar closure still block completion. Do not
interpret portable stage diagnostics as successful isolation or start F23.

#### Effective-controls stage rejected on the operator host

The operator's next positive pair on `e8a29a1ad36a0bf4f982b1d84e5d89a119ef7b03`
is retained in ignored
`state/f22-qualification/operator-results-scryntic-f22-stage-20261009T231436Z/`.
JUnit records **two failures**, no passes/errors/skips, **2.614 seconds**, and
the pipeline status is 1. Both units started at 2026-10-09 23:14:38/40 UTC and
then exited **73**: effective-control inspection/verification or boundary probes
failed before inference. systemd's `CANTCREAT` label is its conventional name
for numeric exit 73, not evidence of a failed file-creation operation. No units
remained; staging before/after contained the same two pre-existing paths
(`a7x8wijv`, `2nmqle25`). No additional SELinux change or root execution by Codex
is recorded. The failing control is still unknown; this pair does not qualify
either profile.

To distinguish the actual failure without exposing values, the trusted
bootstrap now passes its internal stage marker through control inspection,
verification and boundary probes. The existing checks remain in their original
order with the same rejection conditions; missing fields are rejected before
member access, and the coordinator still verifies successful evidence without
a diagnostic marker. Only fixed integers cross process exit; no report values,
environment keys/values, host paths, exception text or payloads are printed.

| Exit status | Fixed effective-control category |
| --- | --- |
| 80 | Evidence field set/type |
| 81 | Per-job UID/GID/groups |
| 82 | Namespace separation |
| 83 | Resource limits |
| 84 | cgroup memory/swap/tasks/CPU controls |
| 85 / 86 / 87 | NoNewPrivileges / seccomp / capabilities |
| 88 / 89 / 90 | Read-only mounts / private writable mounts / tmpfs bounds |
| 91 | Exact allowlisted environment |
| 92 / 93 | PID visibility / extra inherited descriptors |
| 94 / 95 | Probe completion / real profile thread policy |
| 96 / 97 / 98 / 99 | Denied host paths / request and forbidden scratch writes / sockets / fork |
| 100 / 101 / 102 | Immutable model/runtime / bounded threads / allocation limit |
| 103 / 104 / 105 / 106 | Status read / mount inspection / cgroup read / evidence collection |

Statuses 70–77 keep their earlier meanings; 78 remains the fallback for invalid
stage state, and 79 or values outside the two allowed ranges cannot become
success. The narrower statuses are diagnostic hints only, not successful
qualification or attestation. Native termination and failures outside this
entrypoint can still produce other statuses. Repeat the positive pair on the
reviewed candidate; do not run the evaluation or grant new host permissions
until it succeeds and the mandatory 31-probe suite is independently reviewed.

Control-diagnostic candidate validation (ordinary UID; no root jobs):

- Regression-red log records 21 expected failures before instrumentation;
  final focused run has 36 passing cases. A separate portable comparison of
  172 synthetic evidence variants against the preceding committed checker
  gives identical acceptance/exception outcomes, not host evidence.
- Final `scripts/check.sh`, reviewed uv 0.12.18: status 0, **1,718 passed /
  31 host skips**, 97.45 seconds; locks, lint/format, strict typing (273 files),
  packaging and all dependency audits pass. All seven negative controls reject
  their deliberate defects and preserve their inputs, status 0.
- Semgrep 1.179.0: 151 local Python rules / four changed files, zero findings
  or errors. Trivy root/forecast locks including dev plus configuration/secret
  scanning: zero findings; no supported IaC files detected, status 0.
- The first full local Sonar analysis raised three attributable S5778 test
  clarity findings. Fixture creation moved outside `pytest.raises`; all 57
  control-file tests and the final full gates pass afterward. Reanalysis at
  **2026-10-09 23:31 UTC** reports **81.2%** coverage, **0%** duplication,
  **ERROR** gate / scanner status 3, and exactly the same six previously
  recorded open issue keys. The three new findings are absent from the final
  open findings. No rule, exclusion, threshold or disposition was weakened.

Logs and scanner metadata are ignored in
`state/f22-qualification/control-diagnostic-validation/`. Sonar is a full local
default/main candidate analysis based on `e8a29a1` plus these source edits,
not a published GitHub/main/PR validation. Missing successful host controls,
the full 31 probes, real offline comparison and the existing Sonar gate remain
completion blockers.

#### Writable mount conflict confirmed; bounded scratch paths relocated

The operator repeated the positive pair on `944b0b6624df7211419366ac5ad3614710aa3332`.
Retained `operator-results-scryntic-f22-controls-20261009T233400Z/` records two
failures, no passes/errors/skips, **2.656 seconds**, pipeline 1. Both units
started at 2026-10-09 23:34:02/04 UTC and exited **89**, rejecting the private
writable mount evidence before inference. No units or additional staging paths
remained. This identifies the failed category, not the individual host mount
flags or a successful qualification.

The selected unit combined `ProtectHome=yes` with `TemporaryFileSystem=/home`,
and `DynamicUser=yes` with `TemporaryFileSystem=/tmp`. Context7's systemd
documentation and the [version-pinned systemd 259 execution manual](https://github.com/systemd/systemd/blob/v259/man/systemd.exec.xml)
establish the inaccessible home and implicit disconnected PrivateTmp settings.
The [namespace implementation](https://github.com/systemd/systemd/blob/v259/src/core/namespace.c)
orders same-path mounts by mode and drops duplicates: inaccessible home and
private-temp bindings take precedence over the requested tmpfs mounts. This
confirms a configuration conflict consistent with the host's category 89;
the earlier declaration-only portable checks could not qualify those mounts.

The fixed profiles now put HOME on **/worker-home (1 MiB)**, TMPDIR on
**/worker-tmp (16 MiB)**, and retain **/output (1 MiB)**. All three still require
private tmpfs with `rw,nodev,nosuid,noexec` and the same exact measured size.
`ProtectHome=yes`, `DynamicUser`, its implicit PrivateTmp, all namespaces,
read-only inputs/runtime, capabilities, credential filtering and resource
limits remain enabled. `InaccessiblePaths=/tmp /var/tmp` prevents those implicit
temporary areas becoming an additional writable/unbounded escape from the
scratch contract; boundary probes now require writes to both to be denied.
Mount targets are precreated beneath the private staging root. Existing
read-only host binds and shared contracts are unchanged; the repair adds no
elevated helper or model-specific service changes. The environment allowlist
explicitly selects the new HOME/TMPDIR and Torch's existing HOME setting follows
the same fixed profile.

The first candidate scan raised two new S5443 findings at the denied-scratch
probe. Source review also identified an actual probe ambiguity: appending to a
predictable leaf could follow an existing link or mistake a leaf's permission
denial for a non-writable directory. The probe now attempts fresh exclusive
creation with `tempfile.mkstemp(dir=...)`, immediately unlinks the empty file
and closes its descriptor if creation unexpectedly succeeds, then fails
preflight. Cleanup errors after successful creation are isolation failures,
never accepted denials. Context7 and the [Python 3.12 mkstemp contract](https://docs.python.org/3.12/library/tempfile.html#tempfile.mkstemp)
confirm exclusive creation and caller-owned cleanup requirements. A portable
regression verifies that a writable scratch directory is rejected, an existing
symlink's outside sentinel remains unchanged, and the fresh probe is removed.
This is independent probe validation, not evidence that host `/tmp` is denied.

An authenticated read-only SOFA lookup found a JVM PrivateTmp diagnostic post
with a negatively verified reply; its attach/network advice does not establish
mount precedence or apply to this offline worker and was not adopted. The
version-pinned upstream source is the basis for this repair. Actual Fedora
runtime enforcement remains unqualified until the operator repeats the
positive pair, then all 31 mandatory probes and the same-case offline evaluation.

Final portable validation of the relocated scratch/probe candidate (ordinary
UID, reviewed uv 0.12.18; no Codex root/system-manager execution):

- Two profile regression cases failed before the configuration repair; three
  focused profile/staging cases passed afterward. The fresh-file/link regression
  failed before its helper existed and passed afterward. A separate injected
  unlink-permission failure confirmed fail-closed cleanup and a closed descriptor.
- `bash scripts/check.sh`: status 0, **1,721 passed / 31 host skips**, 95.13
  seconds; lint/format, typing, lock, packaging and dependency audits pass.
  `uv run --locked --no-sync python scripts/check_negative.py`: all seven
  deliberate failures rejected, inputs preserved, status 0.
- Semgrep 1.179.0, 151 local Python rules / six affected files: zero findings
  or errors. Trivy root and forecast locks including dev dependencies plus
  secret/configuration scanning: zero findings, status 0; no supported IaC.
- Final full local SonarQube default/main candidate analysis at **2026-10-09
  23:48 UTC**, based on `944b0b6` plus these edits: coverage **81.2%**,
  duplication **0%**, gate **ERROR**, scanner status 3, **seven open findings**.
  Five prior findings remain (S7497 x3, S2612, S8997). The former profile S5443
  key `c9b92aaa-a683-4fa9-86be-64c6d27a2129` is absent after relocation. Two
  S5443 findings remain at the explicit forbidden-directory probe:
  `54bb87d9-b818-4605-9b74-d1579e562419` and
  `555abbe3-894f-4450-9b82-4a8e2939bc20`. Their source now uses exclusive
  fresh creation, no payload write or existing-link following, immediate cleanup
  and fail-closed handling; the regression and cleanup-fault evidence support
  review, not an automatic finding disposition or successful host denial.
  The earlier candidate's two keys were replaced by these keys during analysis.
  No rule, exclusion, threshold, control or server disposition was weakened.

Actual logs and scanner metadata are ignored in
`state/f22-qualification/scratch-mount-validation/`; the final snapshot is
`sonar-after-probe-repair.json`. These portable results do not qualify the
relocated host mounts, either CPU runtime, cancellation/restart, real Bybit
execution or resource/artifact evidence. The seven open Sonar findings and
mandatory host/evaluation evidence remain completion blockers. KER-26 stays
In Progress; F23 must not start.

#### Strict environment rejection after scratch relocation

Retained `operator-results-scryntic-f22-scratch-20261009T235506Z/` is the
operator's positive pair on `24178e6b99a305ac9727bf6c5a9e832ee6556a08`:
**two failures**, no passes/errors/skips, **2.627 seconds**, pipeline status 1.
Both units started at 2026-10-09 23:55:07/10 UTC and exited **91**. The trusted
preflight reached its exact-environment check after accepting the preceding
mount checks; no full effective-control report or inference succeeded. No
units remained and staging before/after was unchanged. This is progress through
preflight, not completed mount/runtime qualification.

Source review identified an omitted per-service environment source: systemd
259's [build_environment and final filtering implementation](https://github.com/systemd/systemd/blob/v259/src/core/exec-invoke.c)
can generate `MEMORY_PRESSURE_WATCH` / `MEMORY_PRESSURE_WRITE` independently of
the manager's `show-environment`. Its [execution manual](https://github.com/systemd/systemd/blob/v259/man/systemd.exec.xml)
documents those variables and final `UnsetEnvironment` filtering. A read-only
ordinary-UID query returned `DefaultMemoryPressureWatch=auto` on this host.
Context7 confirms the distinct generated-environment source/merge semantics.
A portable Python 3.12.14 invocation with the exact synthetic allowlist and
`-I -S -B` retained exactly those keys; Python locale coercion did not add a key.
The host's rejected values were deliberately not printed or accepted, so status
91 alone does not prove which extra keys caused this specific attempt.

The fixed launcher now explicitly unsets both pressure variables before worker
exec, retaining the exact existing allowlist, manager-variable filtering and
coordinator/preflight equality checks. Neither extra variables nor differing
values become acceptable. Memory/cgroup limits and all isolation settings stay
unchanged; the fix does not disable resource enforcement or permit pressure
sockets/credentials. Unknown generated variables still fail closed.
Two regression cases (both CPU profiles, empty manager environment) failed
before the change and pass afterward. Two evidence-rejection cases require
either pressure variable to retain status 91 and the fixed sanitized error.
The focused launcher/control suite passes.

A read-only SOFA lookup of a systemd environment smoke-test post distinguishes
shell, manager and explicit unit environments; it concerns user-manager tests
and does not independently establish this system-service pressure behavior.
No suggested host experiment was run by Codex. Version-pinned upstream source
and the portable regression establish the omission; the operator's next
positive pair must verify its actual host effect. F22 remains unqualified.

Environment-filter candidate validation (ordinary UID; uv 0.12.18):

- `bash scripts/check.sh`: final status 0, **1,725 passed / 31 host skips**,
  **95.60 seconds**; lint/format, strict types (273 source files), locks,
  packaging and dependency audits pass. An initial format gate failure in the
  new test was corrected; its failed log is retained, not counted as a pass.
- `uv run --locked --no-sync python scripts/check_negative.py`: all seven
  negative controls reject their defects and preserve inputs, status 0.
- Semgrep 1.179.0, 151 Python rules / three changed code/test files: zero
  findings or errors. Trivy root/forecast locks including dev dependencies plus
  secrets/configuration: zero findings, status 0; no supported IaC files.
- Full local Sonar default/main candidate analysis at **2026-10-10 00:01 UTC**,
  `24178e6` plus these edits: coverage **81.2%**, duplication **0%**, gate
  **ERROR**, scanner status 3. Exactly the same seven open issue keys remain;
  no new findings or rule/exclusion/disposition changes. This is local candidate
  evidence, not a published-main or PR analysis.

Actual logs and final Sonar metadata are ignored in
`state/f22-qualification/environment-validation/`. No privileged worker
execution occurred in these checks. The next required evidence is the positive
host pair on the reviewed commit, then all 31 probes and offline Bybit evaluation;
the existing seven open Sonar findings/gate also remain blockers.

#### Write-boundary rejection after environment filtering

Retained `operator-results-scryntic-f22-environment-20261010T102048Z/`
is the operator's positive pair on
`ec5941c98ce4618e86368f7eeda3b2bb03fc6374`: **two failures**, zero
passes/errors/skips, **4.361 seconds**, pipeline status 1. Both services started
at 2026-10-10 10:20:49/53 UTC and exited **97**. No units remained; both staging
inventories were empty. The exact-environment check and preceding preflight
checks were accepted, but no complete effective-control report or inference
succeeded. Status 97 groups immutable-request and forbidden `/tmp` / `/var/tmp`
write probes; it does not identify the operation or prove its failure reason.

The next candidate adds only fixed operation/error exit categories to those
three probes. It delegates to the unchanged `denied` policy: only EPERM, EACCES,
EROFS or ENOENT satisfy a denial. Successful creation, cleanup failure and other
errors still fail closed. No exception text, paths, environment values or
payloads are published. Existing 70–77 / 80–106 stages remain supported; gaps
and invalid stages retain silent fallback 78, and successful completion alone
returns 0. These categories are diagnostic hints, not effective-control evidence.

| Operation | Exit-code base |
| --- | --- |
| Immutable request write | 112 |
| Forbidden `/tmp` write | 128 |
| Forbidden `/var/tmp` write | 144 |

Add the following fixed offset to the operation's base:

| Offset | Failure category |
| --- | --- |
| 0 | ENOTDIR |
| 1 | ENOSPC |
| 2 | EMFILE |
| 3 | EFBIG |
| 4 | ELOOP |
| 5 | EEXIST |
| 6 | ENOMEM |
| 7 | EINVAL |
| 8 | EISDIR |
| 9 | Other OSError |
| 10 | MemoryError |
| 11 | Other exception, including scratch cleanup failure |
| 12 | Write operation unexpectedly succeeded |

Focused regression tests first failed without these diagnostics (51 write-helper
cases and six native exit-code cases). After implementation, **166 focused tests
pass** with no skips. They preserve all four accepted denial errnos and verify
silent native exits for valid categories, invalid gaps and exception sentinels.
A portable ordinary-UID scratch-helper check under the application seccomp
filter succeeded in a fresh writable directory; this checks helper compatibility,
not the system-manager mounts. There is no evidence justifying a filter or
mount-policy relaxation. Primary reference:
[CPython 3.12.14 tempfile implementation](https://github.com/python/cpython/blob/v3.12.14/Lib/tempfile.py),
cross-checked with Context7; explicit-directory `mkstemp` propagates POSIX
creation errors rather than turning every failure into a permission denial.

Candidate logs are ignored in
`state/f22-qualification/write-diagnostic-validation/`. Repeat only the positive
host pair on the reviewed commit before all 31 probes or real Bybit evaluation.
KER-26 remains In Progress; F23 must not start.

Write-diagnostic candidate validation (ordinary UID, restored and hash-verified
uv 0.12.18; no root/system-manager execution):

- `PATH=/tmp/f22-tools/bin:$PATH bash scripts/check.sh`: status 0,
  **1,788 passed / 31 mandatory host skips**, **95.89 seconds**. Both locks,
  lint/format, strict typing (273 files), packaging/profile checks and dependency
  audits pass. The first invocation rejected global uv 0.12.19 after the temporary
  approved executable was absent from `/tmp`; restoring the recorded archive
  resolved that tooling prerequisite without changing pins or global tooling.
- `uv run --locked --no-sync python scripts/check_negative.py`: all seven
  negative controls rejected their defects and preserved inputs, status 0.
- Semgrep 1.179.0: 151 local Python rules, all four changed code/test files,
  zero findings/errors, status 0. Trivy root/forecast locks including dev
  dependencies, secrets and configuration: zero findings, status 0; no supported
  IaC files. These scanner results do not qualify systemd controls.
- Full local Sonar default/main candidate analysis **2026-10-10 10:34 UTC**,
  based on `ec5941c` plus these edits: **80.4% overall coverage / 81.4% new-code
  coverage**, **0% duplication**, **seven open findings**, gate **ERROR**,
  scanner status 3. Five prior keys remain (S7497 x3, S2612, S8997).
  The two S5443 reports now attach to the same forbidden-directory literals at
  controls.py:151 under keys `2b414d4b-c27b-45e4-9fc9-b268d2b77f6e` and
  `da6e63ca-392d-4c89-a3e9-1cacef9e9fc5`; the preceding two keys are absent.
  Source review confirms those paths deliberately exercise denied writes via
  exclusive fresh creation and fail-closed cleanup, with unchanged acceptance
  semantics. Host evidence is still missing; no finding was dismissed, and no
  threshold, rule, exclusion or security control changed. This is a local
  candidate analysis, not published-main/PR qualification.

Raw logs and the final Sonar snapshot are in the ignored candidate directory
above. All 31 host probes, actual offline Bybit inference/comparison, repeated
forecasts, accepted artifact/provenance/resource evidence and the seven open
Sonar findings/gate remain completion blockers.

#### Root-relative forbidden paths: actual write success and focused repair

Retained `operator-results-scryntic-f22-write-20261010T103758Z/` records the
operator's pair on `11730cd56dd840216cc9479eac379d33770cc174`: **two failures**,
zero passes/errors/skips, **2.596 seconds**, pipeline 1. Both services started
at 2026-10-10 10:37:59/10:38:02 UTC and exited **140**: forbidden `/tmp`
creation unexpectedly **succeeded**. The immutable-request write probe had
accepted its denial; the `/var/tmp` write probe was not reached. Units after
were empty and both staging inventories were empty. Preflight correctly
rejected the ineffective boundary before inference/result acceptance.

This establishes an ineffective declaration, not a missing permission to grant.
The [systemd 259 execution manual](https://github.com/systemd/systemd/blob/v259/man/systemd.exec.xml)
specifies that `InaccessiblePaths` defaults to host-relative paths, while `+`
makes them relative to `RootDirectory`. In the
[version-pinned namespace source](https://github.com/systemd/systemd/blob/v259/src/core/namespace.c),
`append_access_mounts` marks unprefixed paths already host-relative;
`prefix_where_needed` therefore does not relocate them, and `drop_outside_root`
removes those outside the worker root. The earlier `/tmp /var/tmp` declaration
did not mask the worker paths. This corrects the earlier assumption that
same-path duplicate ordering alone established the intended mask: path scope
must be correct first. Context7 was cross-checked against this tagged source.

The fixed property is now **`InaccessiblePaths=+/tmp +/var/tmp`** for both
application-owned CPU profiles, with no ignore-missing prefix. The precreated
targets remain required. The existing actual write probes and denial policy are
unchanged; a writable target still aborts preflight. No new writable path,
environment allowance, syscall capability, host bind or result schema is added.
DynamicUser/PrivateTmp, bounded `/worker-home` / `/worker-tmp` / `/output`,
identity, network, credential, resource and model/runtime contracts remain.

A focused SOFA lookup concerned JVM diagnostic sockets under PrivateTmp, not
RootDirectory path scoping; it provided no evidence for this fix. Its network
listener, shared-temp and root namespace-entry suggestions were not adopted.
The authoritative manual/source trace and the actual code 140 establish this
defect. Two profile regression cases first failed for the missing `+` and pass
after the repair. Host qualification of the repaired property is still required.

Root-relative candidate validation (ordinary UID, approved uv 0.12.18):

- Focused suite: **166 passed**, zero skips, **0.517 seconds**. An initial
  sandboxed invocation stalled and was terminated (status 143); it is retained
  separately and is not counted as passing. The ordinary-UID run outside the
  Codex sandbox matches the normal gate environment; no root authority was used.
- `PATH=/tmp/f22-tools/bin:$PATH bash scripts/check.sh`: status 0,
  **1,788 passed / 31 host skips**, **96.53 seconds**; both locks,
  lint/format, strict types (273 files), packaging and dependency audits pass.
- `uv run --locked --no-sync python scripts/check_negative.py`: unchanged
  retry status 0; all seven negative controls reject defects and preserve inputs.
  The initial run stalled after 1,642 tests and was interrupted after 410.42
  seconds, causing status 1 rather than the expected deliberate-test failure.
  Its cause is not established; `negative-interrupted.log` is retained separately
  from the successful `negative-retry.log`. No gate or test was changed to obtain
  the retry result. The interrupted run is not passing or host evidence.
- Semgrep 1.179.0: 151 local Python rules / two changed code/test files,
  zero findings/errors, status 0. Trivy root/forecast locks including dev
  dependencies plus secrets/configuration: zero findings, status 0;
  no supported IaC files.
- Full local Sonar default/main candidate analysis **2026-10-10 10:45 UTC**,
  based on `11730cd` plus these edits: **80.4% overall / 81.4% new-code
  coverage**, **0% duplication**, same **seven open keys**, gate **ERROR**,
  scanner status 3. Existing findings remain attributable to their previously
  reviewed code; none was hidden, dismissed or remediated by this mount fix.
  This is not published-main/PR or host-qualification evidence.

Actual logs and scanner metadata are ignored in
`state/f22-qualification/root-relative-validation/`. Repeat only the two
positive host probes on the reviewed commit before all 31 probes or actual
offline Bybit evaluation. At this pre-host checkpoint, no complete
effective-control report, real forecast, baseline comparison, accepted artifact
or measured model resource result existed yet. The seven unresolved Sonar
findings/gate remain blockers. KER-26 remains In Progress; F23 must not start.

### Positive host checkpoint: 2026-10-10 10:55 UTC

The operator ran the two positive host tests on
`c8a920d44838c3925928f8706adfddebfe3dd14c`. Codex inspected the returned
`revision.txt`, JUnit, status, printed reports, journal and cleanup inventories
in ignored
`state/f22-qualification/operator-results-scryntic-f22-root-relative-20261010T105519Z/`.
JUnit records **2 passed, zero failures/errors/skips, 13.029 seconds**;
the pipeline status is **0**. The journal records three successful worker units
(synthetic, real probe, real repeat); units after and staging before/after are empty.

Both CPU profiles report dynamic non-root identities, zero capabilities,
`NoNewPrivileges=1`, seccomp mode 2, six namespace IDs, PID 1 as the only visible
process, no extra descriptors, exact allowed environments, read-only inputs/code,
and only the three approved writable tmpfs mounts. The synthetic profile reports
256 MiB memory; the real profile reports 4 GiB memory, 8 GiB address-space limit,
zero swap, eight tasks and one CPU quota. Both reports record `probes=passed`;
the real profile also records `pthread-only-bounded`. The host tests validate
result decoding and the real test compares equal forecast points across two
actual offline TimesFM executions on its **synthetic 512-row window**, horizon 24.
This is positive-path evidence, not all 31 mandatory probes or Bybit evaluation.

The first real execution reports worker process CPU **4,248,480,000 ns**,
forecast/result elapsed **4,232,616,384 ns**, and process peak RSS
**2,136,040 KiB** (`getrusage(RUSAGE_SELF)`). Separately, systemd reports
**4.273/4.308 CPU seconds**, **4.342/4.337 wall seconds**, and rounded **1G memory
peak** for the two real units. These are distinct accounting sources and scopes;
the RSS/cgroup peak difference has not been reconciled. Do not use the rounded
journal value as the process RSS or either value as a Bybit resource measurement.

No implementation changed after this run. Portable gates/scanner results above
were not rerun for this documentation checkpoint. All 31 host probes, real
Bybit same-case baseline/fake comparison, evaluation reproducibility and durable
artifact/provenance validation remain outstanding. The seven open Sonar findings
and failed gate remain unresolved. KER-26 stays In Progress; F23 must not start.

### Full host checkpoint: 2026-10-10 11:02–11:09 UTC

The operator ran both complete host test files on the unchanged
`c8a920d44838c3925928f8706adfddebfe3dd14c`. Codex inspected the returned evidence
in ignored
`state/f22-qualification/operator-results-scryntic-f22-qualification-20261010T110255Z/`.
JUnit records **31 passed, zero failures/errors/skips, 386.927 seconds**;
prerequisite and host pipeline statuses are **0**. The recorded eight
lock/configuration/inventory/helper/model hashes match the current local bytes.
Both printed effective-control reports have `probes=passed`; the real profile
reports process CPU **4,285,971,000 ns**, forecast/result elapsed
**4,227,459,361 ns**, and peak RSS **2,135,496 KiB**. These remain measurements of
the synthetic host-test window, not the actual Bybit cases.

The journal contains the expected negative-probe failures: bounded CPU kills
at approximately 5/90 CPU seconds, the real memory probe's unit OOM at its 4 GiB
limit, rejected malformed/link outputs and missing controls, cancellation stop
escalation, and runtime expiry after abrupt coordinator death at 30/180 seconds.
No loaded worker units remain. Ordinary cancellation/restart passed; abrupt
coordinator death proves bounded worker lifetime, not automatic staging deletion.

Staging-before is empty; staging-after records `/tmp/scryntic-model-7ffnrdfk`
and `/tmp/scryntic-model-s720r3vi`. Their observed creation times
**11:03:10/11:06:22 UTC** coincide with the two abrupt-death probes. Source tests
kill their coordinators and check bounded unit lifetime; they do not reclaim the
coordinator's private host snapshot. This matches the previously documented
SIGKILL residual limitation. Operator review/removal and a post-cleanup inventory
are still required; this checkpoint is not evidence of automatic cleanup.

Next: preserve this evidence, clean only the reviewed stale roots after confirming
no model jobs remain, then run stage 2 on a fresh root-owned copy of the retained
Bybit installation. No implementation or model/runtime pin changed; portable
gates/scanners were not repeated for this documentation-only checkpoint. Real
Bybit comparison/artifacts/reproducibility and seven open Sonar findings remain
outstanding. KER-26 stays In Progress; F23 must not start.

### Evaluation preflight: decoder failure before model launch

The operator reviewed the two stale roots (owner UID 0, mode 0700, creation times
matching the abrupt-death probes), removed only those roots, and recorded an
empty post-cleanup inventory. Returned evidence is ignored in
`state/f22-qualification/operator-results-scryntic-f22-qualification-20261010T110255Z-evaluation/`.
Stage 2 then exited **1** in `verify_bybit.py`: the first F19 replay inspection
failed through `LinuxDecoder.analytical` / `bounded_process` with the sanitized
`Restricted decoder failed` error. No TimesFM/fake/repeat reports exist, the
evaluation journal has no model units, and units/staging after are empty. This
is not a forecast failure, zero metric or successful evaluation.

Codex reproduced the retained baseline twice through the unchanged restricted
decoder as ordinary **UID 1000**, outside its filesystem sandbox, on both the
original retained installation and the returned evaluation copy. Both commands
exited **0** with the exact baseline/manifest identities and comparison rows
`[1, 2, 95, 96, 143, 144]`, contexts `[2, 3, 96, 97, 144, 145]`.
The latter actual result is retained in ignored
`state/f22-qualification/decoder-diagnostic/baseline-returned-copy.json`.
This distinguishes a successful ordinary-UID decoder path from the failed
operator-root path; it does not identify the root failure or substitute for
root model qualification.

A one-off ignored `decoder-diagnostic/diagnose_decoder.py` calls only the existing
no-input `LinuxDecoder.probe()`. It observes the existing Popen return code and
at most 4 KiB of startup stderr while preserving the production command,
environment, descriptors, pipe bounds/deadline, bootstrap restrictions and
cleanup. It opens no dataset or model and changes no tracked implementation.
Helper SHA-256:
`d78ea2f539474d0aac1ebdc99ff21474e46df8bbe124f4ce32c7866db51959a8`.
Its actual ordinary-UID trial exited **0**, with probe passed, bubblewrap return
code 0 and empty stderr. The operator's root diagnostic then exited **1**:
bubblewrap returned 1 and reported `Can't find source path` / `Permission denied`
for the CPython prefix beneath the operator's private home. This latest result
is operator terminal evidence; its returned evidence directory has not yet been
inspected. The failure precedes Python startup and native parsing.

Context7's `/containers/bubblewrap` documentation and the exact
[bubblewrap v0.12.0 source](https://github.com/containers/bubblewrap/blob/v0.12.0/bubblewrap.c)
were consulted for capability dropping, UID maps and source-path resolution.
The host's `/home/void`, `.local`, and `.local/share` have mode 0700. Root loses
its discretionary-access override during bubblewrap setup; unlike UID 1000 it
does not own these directories. Together with the successful ordinary-UID probe,
this explains the denied source traversal. The version's `--ro-bind-fd` also
resolves its `/proc/self/fd` source back through `realpath`, so it was not adopted
as an unverified shortcut around the private ancestors.

An ignored, one-off `decoder-diagnostic/prepare_coordinator.py` prepares a fresh
private qualification copy of CPython, the coordinator venv, source and scripts.
It verifies content, link and mode tree fingerprints before relocation and
checks that sources did not change during copying. Only the venv interpreter
link, `pyvenv.cfg` home and editable package path are relocated. It starts no
worker, service or privileged runner. Root destinations are restricted to a
fresh `root-coordinator` below an operator-owned 0700 `/var/tmp/scryntic-f22-*`
evidence directory. No home permissions are broadened; the production decoder
command and its selected read-only mounts remain unchanged.

The ordinary-UID copy passed the no-input decoder probe (return code 0, empty
stderr), and reproduced the exact retained Bybit baseline twice through native
restricted decoding. Actual output is ignored at
`decoder-diagnostic/relocated-baseline.json`. The helper passed Ruff. Root
preflight on the relocated environment is still pending; this is not model
qualification and does not close the Bybit criterion. Helper SHA-256:
`063d192c7c815ee8166beb951964c8575be3e88b8830a525f7719bcda86980f4`.

The current SOFA skill was subsequently fetched via the public primary site
and its SHA-256 matched live guidance. A read-only search for bubblewrap root
private-home access returned container namespace posts, with no directly
applicable guidance; none was applied or represented as validation.
No control, UID mapping, input permission, SELinux policy or
native-parser boundary was weakened. All 31 model host outcomes remain valid
for the unchanged source; actual Bybit qualification and Sonar closure remain
outstanding. KER-26 stays In Progress; F23 must not start.

### Relocated root preflight: 2026-10-10 11:54 UTC

Returned evidence at
`state/f22-qualification/operator-results-coordinator-preflight-20261010T115414Z/`
was independently read and checked. Snapshot creation ran as UID 0; its four
tree fingerprints match the ordinary-UID trial. The unchanged no-input decoder
then passed as UID 0, bubblewrap returned 0 and captured stderr was empty.
`verify_bybit.py` reproduced the baseline twice, with the exact manifest and
baseline identities, six comparison rows and context lengths recorded above.
The overall coordinator preflight status is **0**. This confirms root decoding
in the private relocated environment without broader home permissions or
sandbox exceptions. It does not yet qualify TimesFM on Bybit.

An ignored operator-only `state/f22-qualification/run_relocated_evaluation.sh`
now resumes the three real evaluation reports from this exact qualified copy.
It requires revision `c8a920d`, clean implementation paths, the successful host
and coordinator statuses, and identical script/lock bytes. It uses a fresh
output directory, preserves the prior failed attempt, runs the copied script
and interpreter with a cleared coordinator environment, and stops subsequent
model evaluations on the first failure. Journals, unit/staging inventories and
the quiescent dataset catalogs/accepted artifacts are exported in a new small
evidence copy; the CPython/venv snapshot is not duplicated. Bash syntax passed;
this script has not been executed by Codex or qualified by the operator yet.

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
F22_REV=REPLACE_WITH_REVIEWED_COMMIT  # exact commit supplied in the handoff
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
sudo -- journalctl --utc --no-pager --since "$F22_HOST_STARTED" \
  --unit='scryntic-model-*' \
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

For the current installation, use the relocated operator wrapper above. The
original recipe below records the initial workflow: its root dataset copy
already exists, and its original coordinator interpreter was demonstrated to
fail native decoding through the private home ancestors. Do not replay that
copy/launch block on this resumed qualification.

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
from user-owned catalogs. The seven Sonar findings need an authorized review of
the stated source evidence and any host evidence relevant to mount/cancellation
behavior; proposed dispositions are not resolved findings. The configured MCP
is read-only. Validate any actual defect before repair; repeat relevant host
probes and normal gates/scans after source changes. Only when all mandatory
evidence and the unchanged quality gate are resolved may KER-26 move to **In
Review**. Do not start F23, create a documentation-only PR or mark Done.
