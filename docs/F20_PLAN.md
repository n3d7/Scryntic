# F20 / KER-24 — Provider admission and durable jobs

Base: main `09ca623af3a94b7e48c18077ffc6bae5d7d08916`. KER-13/F09, KER-22/F18 and KER-23/F19 are Done and present in this revision. KER-24 and approved ARCHITECTURE.md define scope.

## Contract and decisions

- Preserve ForecastRequest, ForecastResult, ForecastArtifactRef and F09 local-only validation. Add separately versioned closed JSON job/attempt/response envelopes and an admission-checked F20 artifact path; existing artifact references/readers remain valid.
- Operator-owned immutable reviews pin provider/model origin, publisher, revision, verified artifact hashes, package/code/weight licenses, approved use, model-card/terms references, review date, explicit runtime/loading/network/device/memory requirements. Policy grants bind exact review hashes and intended use. Missing/unknown metadata, mutable revisions, unapproved licenses/use, custom/downloaded code, pickle/native loaders, network/download requirements and unknown loading fields fail closed. F20 registers trusted built-in fake computation only; it does not qualify model isolation or load weights.
- Use F18 DatasetService manifest/pin/inspection APIs for bounded primitive forecast inputs, keeping native decoding in the existing restricted worker. Bind the full DatasetRef and verified forecast origin/interval in persisted requests and results. Do not modify F18/F19 provenance or add live feed imports.
- Own one private, inode-pinned SQLite database and lifetime flock. Explicit BEGIN IMMEDIATE, WAL and synchronous FULL protect job admission, claims, cancellation/deadline transitions and accepted-result registration. Bound stored jobs, request/response bytes, horizon and active executions.
- States: queued, running, interrupted, succeeded, failed, cancelled, deadline_expired. Terminal states cannot be cleared/retried. An identical job key/payload is idempotent; conflicting reuse fails. Startup marks abandoned running jobs interrupted (or expired) without executing them. Explicit resume revalidates exact admission and creates a new fenced attempt. Conditional state/attempt updates prevent stale completion and duplicate success.
- Deadlines are persisted absolute UTC nanoseconds, bounded at admission; active execution also uses a monotonic asyncio timeout. Cancellation is durably committed before signalling execution. Clock rollback/expired deadlines fail closed. Shutdown preserves unfinished work as interrupted, not falsely succeeded/cancelled. No autonomous retry or local-to-remote fallback.
- Install validated immutable forecast objects before transactionally registering success; a crash before registration may leave an unreferenced valid object, which is not a successful job. Reuse immutable objects safely, and expose a job result only from durable succeeded state. Crash tests cover both sides of publication.
- Remote remains disabled by default. Exact policy approval and request allow_remote are both required. The only remote adapter constructs a fixed HTTP loopback route from the operator-selected test-double port. No arbitrary host, URL, auth, ambient cookies/proxies, redirects, compressed response expansion, provider credentials or paid service. Envelopes bind job hash, exact attempt/token/request hash, provider and model revision; duplicate/unknown fields, malformed/nonfinite/mismatched/replayed/oversized outputs fail before artifact acceptance.

## Implementation sequence

1. Focused red admission/contract tests; implement closed contracts, review/policy checks, codec and two built-in fake calculations.
2. Red durable-state tests; implement private SQLite ownership/schema/quotas, idempotent admission, fenced transitions, cancellation/deadlines, restart recovery and explicit resume.
3. Red execution/artifact tests; integrate F18 inputs and application-owned result validation, cancellation/timeout lifecycle and publication crash windows while preserving F09.
4. Red shared local/remote and hostile HTTP tests; implement bounded loopback test double/client and exact response validation.
5. Run focused compatibility/recovery/security tests and canonical project gates plus disposable negative controls. Repair failures without boundary weakening.
6. Full Sonar analysis with fresh coverage; inspect F20/new issues, hotspots, coverage and duplication. Targeted local Semgrep; Trivy project dependency/secret/configuration scans. Validate findings against source and repair attributable defects.
7. Document APIs, transitions, operator approvals, limits and exact validation evidence. Commit/push implementation, open an unmerged PR against main and leave KER-24 In Review after required validation passes.

## Evidence and limits

Context7 consulted CPython and aiohttp primary documentation; Python 3.12 documentation confirms autocommit=True requires explicit SQL transactions and timeout conversion occurs outside asyncio.timeout. SQLite documents IMMEDIATE writer reservation and WAL FULL durability. aiohttp documents streamed bodies, timeout fields, environment proxy trust and auto decompression. SOFA post https://agents.stackoverflow.com/tils/7d036b29-2221-43ec-b6c6-748e379bae32 identifies conditional claim/rowcount and deferred WAL snapshot hazards; its JavaScript API is not adopted. Verify in Python transactions and tests.

F20 promises one durable accepted success, not exactly-once external computation across a network failure. Only explicit safe test-double retry is supported. Cooperative trusted fake cancellation is qualified; F21 owns hostile worker enforcement and F22 owns real model integration. No architecture, gate, exclusion, suppression, financial authority or credential boundary changes are authorized.
