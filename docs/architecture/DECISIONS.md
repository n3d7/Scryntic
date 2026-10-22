# Architecture decision records

Initial decisions: 2026-10-18; MCP addition: 2026-10-19. The operator **approved the architectural direction, ADRs and roadmap on 2026-10-19**. Execution is limited to conservative M0 preparation and PI01 after the complete PI00 policy gate; approval does not waive scientific/legal/security requirements or authorize later tasks/publication. Current gate record retained locally. No measured benefit or delivered redesign is implied. [Canonical architecture](../../ARCHITECTURE.md), [evidence index](../research/WORKING_RECORD.md).

## ADR-PI-001 — Long-horizon forecasting is the first functional product

**Decision:** build hours/days/weeks/longer return targets, baselines and a forward prediction ledger before expanding model count or low-latency infrastructure. Initial proposed horizons: 1 h, 1 d, 7 d, 28 d. Keep the zero-return/historical-mean champion unless a candidate clears preregistered gates.

**Alternatives:** extend the old minute-candle/TimesFM demonstration into the product; prioritize order books/HFT; build a trading bot first. Reject as the organizing roadmap because operational integration and price-level fit do not establish return skill, and execution is explicitly deferred.

**Evidence:** [academic sources 1–8, 13–17](../research/ACADEMIC_EVIDENCE.md) support useful nonlinear models in some settings and serious counterevidence elsewhere. Equity, retail and generic-series results do not choose a crypto winner.

**Consequences:** scientific negative results are retained. Initial universe, data rights, meaningful margin, budgets and forward-sample criteria require policy decisions before search. Revisit horizons only after the core gate or an explicit changed product brief.

## ADR-PI-002 — Evolve the Python modular core; native engines before Rust

**Decision:** retain Python services/coordinator/ML; preserve SQLite owner semantics and immutable Parquet/Arrow. Batch current analytical work, then qualify a native query/feature engine. DuckDB is a shortlisted isolated query engine; Polars an alternative where useful. Rust is conditional on measured bottlenecks.

**Alternatives:** retain every row-wise path indefinitely; replace storage with PostgreSQL/Kafka immediately; adopt a large Nautilus-style Rust core. The first risks avoidable scaling cost; the others add migration and operational cost without a measured need.

**Evidence:** current F18/F19 source and [F10 measurements](../data/performance/f10-operating-envelope.md); [native/source patterns](../research/REFERENCE_SYSTEMS.md); [benchmarks and migration criteria](../research/RUST_BENCHMARKS.md). No Python-versus-Rust benchmark has been run here.

**Reversal:** recorded end-to-end SLO failures after batching/native optimization, or actual multi-writer/availability needs. A microbenchmark, language preference or generic “Rust is faster” claim is insufficient.

## ADR-PI-003 — Point-in-time data is a versioned contract

**Decision:** retain immutable revisions and separate valid/event time from knowledge/availability at a declared plane. Use versioned instrument mappings, universe membership and quality masks. Historical reconstructed evidence stays distinct from as-observed and forward evidence.

**Alternatives:** overwrite corrected rows; use final historical data and timestamp sorting; equate event time with availability. Reject because they introduce look-ahead and make datasets unreproducible.

**Evidence:** existing F18/F19 timing contracts; [ALFRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html); [SOFA panel/availability corrections](../research/ENGINEERING_SECURITY.md). Date-level vintages do not prove intraday availability.

**Consequences:** missing knowledge time is an explicit limitation, never manufactured. Cross-venue mappings and revisions require known/effective intervals. Adapter conformance must include resets, late data, gaps and revisions. Immutable storage does not grant redistribution/training rights.

## ADR-PI-004 — Selection and qualification are independent from research

**Decision:** nested chronological development with common multi-asset origin cutoffs, interval-based purge, justified embargo, training-only transforms, isolated calibration, a complete trial ledger and a sealed final period. Independent qualification and forward shadow evidence precede promotion. Score distributions with proper losses and coverage/width, and report economic cost sensitivity separately.

**Alternatives:** shuffled rows, repeated final-test tuning, one aggregate accuracy number, or foundation-model preference. Reject because they confuse selection with evaluation and hide dependence/contamination.

**Evidence:** [academic evidence 9–17](../research/ACADEMIC_EVIDENCE.md); [Consensus fetched records](../research/consensus/PAPER_RECORDS.json); [FreqAI/NeuralForecast/Chronos source limitations](../research/REFERENCE_SYSTEMS.md).

**Consequences:** final labels are not exposed to research workers; access consumes evidence. Model/environment/data versions and all failed trials are retained. Numeric significance and improvement policies are task-specific and registered before outcomes, not universal constants invented by this design.

## ADR-PI-005 — Incremental enrolled workers with explicit trust

**Decision:** extend durable local jobs to admitted train/evaluate tasks, then an enrolled host agent with leases, fencing, immutable environments, bounded artifact transfer and independent acceptance. Keep transport/provisioning credentials outside offline workers. CPU first; GPU separately qualified.

**Alternatives:** arbitrary SSH shell jobs, shared writable data directories, general cluster framework first, or trust a successful worker response. Reject because none establishes task/attempt identity, resource containment or honest results.

**Evidence:** F20–F22 implementation and host qualification; [engineering experience/corrections](../research/ENGINEERING_SECURITY.md); [architecture threat model](THREAT_MODEL.md). No remote-pool/GPU runtime was tested in this assignment.

**Consequences:** at-least-once dispatch, at-most-once accepted logical outcome; no exactly-once execution promise. Host/kernel/launcher/driver remain trusted. Use trusted scoring/requalification and selected reruns; hashes/signatures do not prove numerical honesty. Revisit multi-coordinator scheduling only after an actual availability need.

## ADR-PI-006 — Bounded autonomy cannot change its authority

**Decision:** researchers may select approved recipes/families/search spaces under resource and trial policies. Qualification and model registry own separate permissions. New code/dependencies/loading modes, software edits, credentials or weaker validation require a distinct reviewed admission path. Initial production promotion is manual; later automation is explicitly preapproved and scoped.

**Alternatives:** autonomous agents rewriting production or unrestricted execution; worker-driven promotion. Reject because a researcher could optimize the acceptance rule or escalate privileges instead of improving unseen forecasts.

**Consequences:** task audit includes hypotheses, trials, budgets, stop conditions and interventions; untrusted news/research content cannot become instructions. Drift can trigger bounded retraining or fallback, never bypass qualification. Controls must fail closed when effective evidence is absent.

## ADR-PI-007 — Shared UX, late private web, separate future trading

**Decision:** preserve F24 application services and CLI/TUI parity. Build a private authenticated least-privilege web adapter near core completion. Financial execution remains in a later independent strategy/risk/executor deployment consuming versioned forecasts.

**Alternatives:** move business logic into dashboard handlers; let forecast providers place orders; make trading necessary for core acceptance. Reject as incompatible with reliability and explicit product boundaries.

**Consequences:** dashboard failure is isolated; VPN does not replace authorization. Default remote access is read-only. [Future trading design](FUTURE_TRADING.md) defines simulation, risk, permissions, reconciliation and kill-switch requirements but adds no active capability. Minute/second work and bot implementation are deferred after core completion.

## ADR-PI-008 — Additive migration and separate roadmap history

**Decision:** keep Foundation Done statuses/history and v1 artifact readers. A new Linear project stages the redesign in Backlog, with explicit approval and core-release gates. Reuse F25–F30 work where relevant without claiming those issues completed or silently inheriting their entire sequence.

**Alternatives:** renumber/reopen completed work; edit old evidence to make snapshots appear current; overwrite old artifacts or Drive copies. Reject because it loses traceability and confuses historical/current states.

**Consequences:** preserve old architecture bytes, remove canonical architecture's ignore rule and document discrepancies. Repository documents are canonical; remote copies are dated references. The operator subsequently waived Drive updates, so the inspected copies remain unchanged. [Migration](MIGRATION.md) and [current-state evidence](../research/CURRENT_STATE.md) define compatibility and remaining gaps. Commit/push remains a separate explicit authorization.

**Authorized follow-up:** the operator then requested that the old Linear program stop competing with the new one, with Done issues untouched. Cancel the old project and unfinished F25–F30 as superseded; preserve descriptions/relations and explicitly carry applicable acceptance into PI17/PI18/PI21 before cancellation. Telegram is optional/deferred outside the active core backlog. This is cancellation of an unfinished plan, not release completion. Readback verified all 45 Done issue snapshots unchanged; [disposition](ROADMAP.md#foundation-disposition-after-operator-follow-up).

## ADR-PI-009 — Optional AI-agnostic MCP over shared application services

Date: 2026-10-19. Architectural direction subsequently approved with the other ADRs; MCP remains a later, unexecuted branch outside the current M0/PI01 implementation authorization.

**Decision:** make an optional official-protocol Python MCP adapter over the same private typed application facade as CLI/TUI/web. Start with read-only tools/resources; allow preauthorized bounded forecast/research submissions and ownership-safe cancellation; sensitive allowlisted configuration changes use independent server-bound preview/approval/apply/rollback. Authorization, audit, admission and durable state stay in application services. No generic dispatch, database/filesystem/shell/code, credential, policy, hidden qualification data, promotion or execution authority.

**Transport and compatibility:** operator-managed local stdio first; authenticated private Streamable HTTP as a separately qualified profile. Research pins current MCP 2026-07-28 and official Python SDK v2.3.0; explicitly test older client profiles rather than assuming automatic fallback. Ordinary durable Scryntic job IDs avoid an initial dependency on protocol Tasks. HTTP OAuth/resource checks and per-action ACLs remain mandatory regardless of client annotations or VPN. ChatGPT Secure MCP Tunnel and hosted Claude reachability are different, conditional deployment profiles; neither is provisioned by this decision.

**Alternatives:** reject early MCP scheduling/storage or collector embedding because they duplicate policy/state or couple credentials and availability. Reject Codex-specific APIs and unrestricted execution wrappers. Deferring all interface thinking until the final adapter risks inconsistent service authority; designing an entire framework in M0 would delay validated forecasting.

**Sequencing:** PI26 is a lightweight non-blocking M3 contract after PI12; PI27–PI29 implement and qualify optional M7 interfaces after the relevant private read/management services. Existing milestones are reused; no new prerequisite is added to PI02–PI04 or PI21. M7's required dashboard and optional MCP branches have distinct release gates.

**Evidence:** [canonical MCP contract and qualification matrix](MCP.md), [official specifications, pinned SDK/extension sources, Context7, OpenAI Developers, Claude and corrected SOFA evidence](../research/MCP_EVIDENCE.md), [trust-boundary addendum](THREAT_MODEL.md#mcp-interface-addendum--proposed-2026-10-19). Current source only establishes typed workflows and per-instance cancellation, not remote authorization. Independent focused design review found no Important/Critical issue; its minor bearer-replay wording correction was applied. No runtime/security/client test was performed.

**Consequences/open decisions:** optional dependency/lock review, client/build/protocol matrix, identity provider/registration, private ingress/egress, numeric quotas and allowed configuration/rollback fields must be settled before enablement. User confirmation at a client is not an approval receipt; snapshots cannot establish future interoperability. Missing eligibility leaves that profile disabled and does not block prediction core.
