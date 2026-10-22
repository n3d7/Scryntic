# F19 / KER-23 implementation plan

Goal: reproducible offline candle forecasts and evaluation over pinned F18 snapshots.
Authority: KER-23 and ARCHITECTURE.md, datasets/analysis/replay and F19.
Base: main 131b7e9; original checkout changes are outside this worktree.

Design decisions:

- Consume F18 recipe 1.2/schema 1.1 through DatasetService's restricted,
  bounded primitive inspection. Require an active dataset pin. No native file
  parser, live connector, model/provider or execution authority is added.
- Use a synthetic monotone clock at candle end plus an explicit decision delay.
  Historical reconstruction uses candle close boundaries; as-observed additionally
  requires latest plausible receipt <= earliest plausible decision. A selected
  later revision is excluded, never substituted with an invented earlier value.
- Keep labels outside replay features. Check contiguous F18 lag/label lineage,
  quality and coverage for every dependency. Score labels only at an explicit
  partition cutoff. Preserve F18 exclusions and derivation gap reasons.
- Use absolute half-open train/validation/test decision ranges. Purge labels
  touching/crossing the next boundary, labels observable only after that boundary,
  and decisions whose uncertainty straddles a boundary. No random split.
- Fit population standardization per candle series on eligible training features
  only; freeze the fitted state for validation/test. Constant features use scale 1.
  The naive statistical baseline forecasts the last available close. Report MAE,
  RMSE and signed prediction-minus-actual bias per series and split in price units.
- Fixed Decimal context (precision 80, half-even), no randomness, wall-clock reads,
  imputation, fitting on evaluation data or parameter search. Canonical JSON binds
  dataset/Parquet hashes, configuration, code/lock revisions, fit rows, predictions,
  exclusions, metrics and limitations. Decimal results remain strings.

Implementation and validation:

1. Write failing pure clock/reader/split fixtures, including late backfills,
   uncertainty, future lag values and overlapping labels. Implement typed replay
   contracts in src/scryntic/replay/{contracts,reader}.py.
2. Write failing repeatability/train-only/metrics fixtures. Implement frozen
   standardization and baseline evaluation in replay/evaluation.py.
3. Add replay/service.py over DatasetService; test real restricted F18 import,
   build, pin, inspection, repeat evaluation, corruption and retired-pin rejection.
4. Document the application API, exact semantics, limitations and references.
   Run focused tests, scripts/check.sh and check_negative.py. Inspect complete
   F19 issues/hotspots/coverage/duplication via SonarQube MCP after full analysis;
   fix attributable findings, regenerate coverage and repeat affected gates.
5. Review the final diff/untracked files, record validation, commit/push the
   implementation branch and create a PR against main. Leave KER-23 In Review.

Review focus: empty/constant training data; multi-series separation; late coverage
repair; forged or inconsistent derived lineage; all observations excluded.
All are tested by the owning component. No new dependencies or gate changes.

Outcome: implementation, tests, documentation and confirmed repairs completed.
Normal gates: 1385 tests passed; seven negative controls passed; final local-tree
Sonar gate OK with zero new issues. See F19_VALIDATION.md for exact evidence and
publication/review state. Independent review's nonfinal-lag finding was fixed and
re-reviewed; no architecture or prerequisite change was required.
