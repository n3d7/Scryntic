# Full SonarQube findings repair

**Goal:** Review the authoritative full-project analysis, repair every confirmed actionable finding, and evidence the final server state without changing quality policy or architecture.

**Baseline:** `main`, revision `131b7e9a6e4feeac62cce9d6f17498dda14afbe3`; clean tree. Normal gates passed (1339 tests). Full scanner analysis at `2026-10-03T16:47:44Z`: 133 open issues, zero Security Hotspots, coverage 79.1%, duplication 0.5% (97 lines, six blocks across two SQLite stores). Quality Gate OK with no evaluated conditions on this first analysis of the replacement server.

**Constraints:** Preserve exact dataset/schema references, provenance and temporal cutoffs, restricted native decoding, cancellation, SQLite durability and clear domain boundaries. Preserve user changes. No thresholds, exclusions, severities or rule suppression changes. No publication or merge as part of this review.

## Work

- [x] Run `scripts/check.sh` with pinned uv; regenerate coverage; scan from repository root and wait for the gate.
- [x] Retrieve complete open issues, hotspots, metrics, uncovered-file details and duplicated-block details through the official read-only SonarQube MCP.
- [x] Repair archive/dataset/import/sync findings; verify exact acceptance and trust boundaries.
- [x] Repair ingestion/normalization/publication findings; inspect each duplicated SQLite block against domain ownership.
- [x] Repair source/recovery/clock/configuration findings; verify TLS and cancellation against actual code and primary documentation.
- [x] Repair ambiguous exception tests and benchmark/audit findings; add meaningful tests for uncovered behavior.
- [x] Review all changes and record evidence for genuinely non-actionable findings.
- [x] Run focused checks, normal gates, fresh full analysis and re-read complete MCP results. Repeat for remaining actionable findings.
- [x] Record initial/final metrics, commands, limitations and exact remaining issue rationale in the review record.

Independent workers own disjoint production domains. The coordinator owns existing flagged tests, scripts/benchmarks, evidence aggregation and final validation.
