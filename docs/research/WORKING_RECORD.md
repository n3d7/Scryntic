# Predictive Intelligence redesign — working record

Started: 2026-10-18. Status: the operator approved the architecture/ADRs/roadmap on 2026-10-19 and authorized conservative M0 preparation plus PI01 after the full PI00 policy gate. PI00 remains open; no redesigned product implementation has begun. See the local-only data-rights checkpoint and local-only execution record. Later tasks, trading, repository publication and Drive access remain outside this execution.

## Intent and evidence rules

Maximize validated out-of-sample return forecasting, starting with hours, days, weeks and longer horizons. Preserve Foundation durability, provenance, recovery, isolation and application-service guarantees. Distinguish source facts, historical validation, research evidence, hypotheses and recommendations. Repository documentation is canonical; Drive should point to it rather than become a second editable specification.

## Work queue

1. Reconcile local/GitHub implementation, validation reports, Foundation Linear issues and Drive history.
2. Investigate academic evidence with Consensus and primary papers; inspect seven requested reference repositories at immutable revisions; consult SOFA and version-specific Context7 docs.
3. Compare architecture alternatives, Python/native/Rust placement and secure worker boundaries; perform a Codex Security architecture review.
4. Write target ARCHITECTURE.md, decision records, evidence, compatibility/migration plan and staged roadmap.
5. Create a separate Linear program without rewriting completed Foundation history. Drive was inspected read-only; the operator later waived updates and no Drive write was performed.
6. Review consistency, links, scope and actual external changes; report remaining uncertainties and stop before implementation.

## Initial observations

- Local branch `main`, HEAD `8e1465fd389e440763ef69b7c59832ad2f8bd8de`.
- Pre-existing untracked `docs/validation/F01_F20_REVIEW.md` is preserved, not an output of this assignment.
- Existing ARCHITECTURE.md is ignored/untracked and still says there is no implementation (2026-09-14 baseline). Current tracked documentation includes F18–F24 validation reports. This is a material documentation discrepancy, not evidence that completed features are missing.
- Code graph project `home-void-Project-Scryntic`: ready, 4,931 nodes/33,619 edges; status exposes no generation timestamp. Tier 2 task-directed verification. No reported partial/skipped parses; ignored documentation requires direct reading. Graph coverage is a best-effort signal, not exhaustive proof.

## Decisions and uncertainties

- Research and proposed roadmap can proceed under the explicit assignment; architecture approval is required before redesigned product implementation.
- No remote host workload is currently necessary. Debian lab is available if a later evidence gap warrants a bounded qualification; no new benchmark performance claims will be invented.
- Initial evaluation universe, budgets, data rights and meaningful improvement margins will be explicit proposed policy parameters, not fabricated empirical facts.

## Evidence and decisions recorded

- [Current state](CURRENT_STATE.md): local/GitHub HEAD agrees; initial Linear snapshot had 51 Foundation issues, F01–F24 Done and F25–F30 Backlog. Under the operator's subsequent explicit consolidation request the project/six unfinished issues are Canceled as superseded; all 45 Done snapshots remain unchanged. Old architecture and F24 publication prose are stale; original history is preserved.
- [Academic evidence](ACADEMIC_EVIDENCE.md): 17 sources, contradictory findings, study scopes and read-depth limitations. [Consensus log](consensus/SEARCH_LOG.md) and [fetched records](consensus/PAPER_RECORDS.json): 9 attempted searches, 6 successes/3 rate limits, 8 unique fetched papers with retained metadata and original research paraphrases, plus 40 discovery-result identities/ranks. Full copyrighted abstracts/search snippets are not reproduced. Firecrawl search failed for insufficient credits; accessible primary papers were read through web.
- [Reference systems](REFERENCE_SYSTEMS.md): all seven requested repositories inspected at immutable revisions; source-level components, adoption costs and licenses. Cryptofeed's inspected current license is AGPL; no code is copied/adopted. Weight/data rights remain distinct.
- [Engineering/security](ENGINEERING_SECURITY.md): SOFA authenticated reads, 7 searches/6 consulted posts including negative/correcting replies; independent primary checks, Context7 and OpenAI Docs. Context7 DuckDB/PyO3 references are floating; pinned primary PyO3/PyTorch evidence and implementation qualification requirements are explicit.
- [Threat model](../architecture/THREAT_MODEL.md): Codex Security architecture workflow/source review; advisory TAC query not granted. No full scan, runtime exploit test or distributed/GPU qualification is claimed.
- [Rust plan](RUST_BENCHMARKS.md): Python/native first, measured selective Rust only; no new benchmark executed.
- [Focused Consensus follow-up](CONSENSUS_FOLLOWUP_2026-10-18.md): seven further fetched studies across five searches, capped at 12 total tool calls. Popular tabular work, crypto basis/factor limitations, LLM-news diagnostics and the original SPA/recursive-refit incompatibility refine the research shortlist. [Separate records](consensus/FOLLOWUP_RECORDS.json) preserve queries, dated citation counts, corrections and read depth. No new model is admitted or roadmap issue changed by this supplement.
- [Target](../../ARCHITECTURE.md), [ADRs](../architecture/DECISIONS.md), [migration](../architecture/MIGRATION.md), [roadmap](../architecture/ROADMAP.md) and [future trading](../architecture/FUTURE_TRADING.md): draft 11 stages/26 Backlog items. Local training precedes model tournament; hidden holdout/trial ledger precedes broad search; core completion precedes minute/second and trading work.
- The original Foundation architecture is preserved byte-for-byte: SHA-256 `7f2ca01a13f1ac54568a3eb2dad2950a915b17db672aadd6e407fc0d187fbbab`. Its relative links retain their original repository-root context.
- User explicitly paused work for a permission-setting change, then resumed; all research agents were interrupted during the pause. No dependent work proceeded while paused.
- Independent architecture review found three prerequisite gaps and verified their resolution: M1 adds a fixed isolated baseline fit/evaluate task, PI02 gates cadence/history and bounded resampling/duration labels, and M0/PI01 reserve final data and trial/access accounting before the first fit. General training/registry expansion happens later without retroactively labeling inspected data untouched.
- Initial delivery of the [Linear program](https://linear.app/kerigan/project/scryntic-predictive-intelligence-47ca383cd656): Planned, 11 milestones, 26 new Backlog issues KER-60–KER-85, 35 blocking edges. Every issue's status/milestone/dependencies read back successfully. At that initial checkpoint all 51 Foundation issue statuses were unchanged; the later authorized disposition is recorded below. [Exact ledger](../architecture/LINEAR_ROADMAP.json).

### Operator-authorized Foundation consolidation

After the initial redesign delivery, the operator requested retiring the competing Foundation queue while leaving Done untouched. The old project and six unfinished F25–F30 issues are Canceled as superseded, not completed. Before cancellation, applicable reporting/deployment/retention, extension and release gates were explicitly carried into PI17/KER-77, PI18/KER-78 and PI21/KER-81 (including the 72-hour soak/offline-volume qualification); Telegram stays optional/deferred. Original descriptions/dependencies are retained. Readback verified 45 Done issues unchanged across returned fields, six Canceled, and the new 26-item Backlog intact. [Disposition and mapping](../architecture/ROADMAP.md#foundation-disposition-after-operator-follow-up).

## MCP planning extension — 2026-10-19

The operator authorized architecture/roadmap planning for an AI-agnostic MCP interface, with no product implementation/runtime install/commit/push. [Evidence ledger](MCP_EVIDENCE.md) records current official MCP/SDK GitHub sources, Context7, OpenAI Developers/client compatibility, SOFA caveats and Codex Security review limits. [Canonical design](../architecture/MCP.md), ADR-PI-009 and the threat-model addendum define controlled tools/resources, server-side authority, independent approval and failure isolation. Plan PI26 as a non-blocking M3 contract and PI27–PI29 as an optional M7 interface branch; no new early-forecast or PI21 gate. Tracker write/readback status is in `LINEAR_ROADMAP.json.external.mcp_extension`; no deployment/interoperability evidence is implied.

## Next step

Review [delivery verification](VALIDATION.md) and the target architecture with the operator. Product tests/benchmarks are not rerun for a documentation-only change; historical validation remains labeled. Commit/push and redesigned implementation require separate explicit authorization. No further Drive action is required.
