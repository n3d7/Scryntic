# F22 / KER-26 implementation plan

Goal: interchangeable offline local forecasting through F20 jobs and the F21
fixed launcher, qualified with Google TimesFM 2.5, without execution authority.

Base: current main `62f9aee731dbac3eadcac03422413e329174498f`.
F19/F20/F21 are merged and Done in Linear; F21's host qualification remains
unperformed (confirmed by the operator). Portable evidence cannot close it.

## Boundaries and decisions

- Built-in, operator-owned model definitions and strict TOML selection; no
  arbitrary import paths, executable configuration, provider marketplace or F28.
- Keep F20 job/response schemas, fencing, input comparison, durability and
  bounds. Resolve additional forecast history through a common validated-window
  boundary, tied to the same immutable F18 reference and last two F20 values.
- History is selected by F19's reader at each decision cutoff. Targets and
  scoring data never enter model input. Reuse F19 cases, purging, exclusions and
  Decimal scoring arithmetic for equivalent persistence/model comparisons.
- Vendor imports exist only in the adapter invoked inside the worker after
  effective-control verification. CPU-only wheels; no remote code, pickle,
  `from_pretrained` Hub access, JIT compilation, downloads or ambient tokens.
- Provision outside inference. Pin the model commit and every artifact hash,
  runtime lock and reviewed wheel sources. Verify/snapshot inputs before launch.
- Preserve the synthetic F21 profile. Add a separately qualified fixed eager
  CPU runtime profile: bounded pthreads only, fork/exec/socket/namespace bans,
  larger candidate memory/address-space/time limits, same identity and mounts.
  Proposed resource limits are not qualified until real host probes pass.
- No GPU profile: this model has a CPU implementation.

## Implementation and validation

- [x] Tests first for selection, rejection, admission, immutable staging and
  preprocessing/leakage; implement model definitions, window and provisioning.
- [x] Lock an isolated TimesFM/PyTorch CPU runtime; inspect official source and
  document package/code/weight licenses and data-only loading identity.
- [x] Tests first for the additional fixed profile and thread-only seccomp;
  implement preflight, actual limits, adapter and bounded result import.
- [x] Integrate the common provider/window boundary with existing JobService;
  prove selection switches between TimesFM and fake without service changes.
- [x] Add an operator qualification/evaluation command using a real Bybit-derived
  F18 dataset, durable F20 forecast artifacts and equivalent F19 scoring.
- [x] Run focused tests, normal gates, SonarQube, Semgrep and Trivy; repair
  attributable defects without weakening policy. Open source-reviewed Sonar
  findings and its unchanged gate remain explicit qualification limitations.
- [x] Attempt available qualification. If system-manager authorization is
  unavailable, stop real inference at that boundary and record the exact manual
  commands, required evidence and missing baseline comparison/resource results.
- [ ] Review diff, commit/push and open an unmerged PR. In Review only after
  actual-model/host qualification and required gates pass; otherwise draft and
  In Progress with explicit remaining work.
