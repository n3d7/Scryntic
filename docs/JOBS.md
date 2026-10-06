# Provider admission and durable jobs (F20)

F20 adds an application-owned job boundary over the existing `ForecastRequest`,
`ForecastResult`, `ProviderDescriptor` and `ForecastArtifactRef` contracts. The
F09 local-only path remains supported. F18 dataset references and provenance are
preserved; F19 replay/evaluation is unchanged. See [ARCHITECTURE.md](../ARCHITECTURE.md)
for the authority and isolation boundaries.

The executable profile contains fixed persistence/trend fixtures and a bounded
loopback HTTP test double. There is no model discovery, weight loader, downloaded
code, arbitrary URL, credentials, platform integration or financial authority.
Real models and hostile-worker isolation belong to F22 and F21 respectively.
The F21 fixed `IsolatedFakeProvider` adapter preserves this contract while moving
the pinned fixture into the fail-closed CPU launcher. Its privileged qualification
is still pending; see [MODEL_WORKERS.md](MODEL_WORKERS.md) and
[F21_VALIDATION.md](F21_VALIDATION.md).

## Admission

`ModelReview` and `AdmissionPolicy` are immutable objects supplied by trusted
application/operator wiring. Provider registration and schema compatibility do
not grant admission. Every submitted/decoded job and every dispatch checks:

| Evidence | Required decision |
| --- | --- |
| Origin, publisher, revision | Known explicit values; mutable labels such as `main`, `head` and `latest` rejected |
| Artifact identity | Nonempty, exact equality between reviewed SHA-256 identities and the descriptor |
| Package, code, weight/model license | Every license explicitly present in the operator allowlist |
| Intended use | Exact allowed use and model usage-policy ID matching the injected policy |
| Model card, terms, review date | Known references and an ISO calendar date, bound by the review digest |
| Loading | CPython 3.12, primitive JSON, built-in code, CPU, declared memory 1–64 MiB, no network/downloads |
| Review approval | Exact review digest in `approved_reviews`; changing any evidence requires a new grant |
| Outbound input fields | Exactly `start_ns`, `interval_ns`, `close` |
| Remote authorization | Both policy `remote_enabled=True` and request `allow_remote=True`; both default false |

An absent/unknown/unapproved field or an unsupported loading profile fails closed.
The job persists the exact review, policy and verified input snapshot. Dispatch
requires equality with the currently injected review/policy and rereads F18 input
primitives. A policy change never silently executes a queued or resumed old job.
The bounded profile rejects pickle, custom/native model loaders, downloaded code,
GPU requirements and network-dependent loading. Memory/device values are admission
declarations, **not** an F21 worker resource-isolation qualification.

Built-in fixture identity is `scryntic:builtin-f20-fake`, publisher `scryntic`,
revision `f20-fixed-persistence-v1` or `f20-fixed-trend-v1`, and the SHA-256 of that
revision's ASCII fixture identifier. These are synthetic fixtures without model
weights. They do not attest a real model file; real artifact verification/loading
must be qualified by the later model work. Execution adapters check the exact
fixture identity independently of admission policy grants.

## Job and result contracts

The closed canonical JSON envelopes use `scryntic.provider-job` version 1.0 and
`scryntic.provider-response` version 1.0. Unknown fields/versions, duplicate JSON
keys and noncanonical representations are rejected. Float values use finite
canonical string representations, consistent with the repository canonical JSON
primitive domain.

A request binds the full original DatasetRef, application forecast request,
verified last-candle timestamp/interval, two finalized closes, intended use,
review/policy, seed, submitted UTC nanoseconds and absolute UTC deadline. Attempt
number and fresh token fence execution. A response binds the exact job digest,
attempt/token and serialized attempt-request digest, then carries the existing
application-owned ForecastResult. The application validates dataset/schema,
provider, model revision, request ID, horizon, timestamps/spacing and finite
values. Provider bytes or vendor objects are never accepted as an artifact.

`F18Inputs` uses DatasetService pins and its existing restricted decoder inspection.
It reads only the last two rows, requires contiguous finalized candles from one
instrument and matching frequency, and rejects unsupported targets/covariates.
It does not import native dataset decoding into the coordinator.

| Bound | Value |
| --- | --- |
| Serialized request/attempt or response | 65,536 bytes each |
| Dataset rows / forecast horizon | 2–1,024 / 1–24 |
| Job duration | Positive, at most 300 seconds |
| Persisted jobs / active tasks | At most 1,024 / default 2, configurable 1–8 |
| Explicit attempts | At most 3 |
| HTTP fixture cache / in-flight bodies | 64 logical jobs / 2 |
| HTTP client timeout | Default 2 seconds, configurable 0.05–5 seconds |
| HTTP request-body timeout | 2 seconds |

The HTTP adapter derives only `http://127.0.0.1:<port>/v1/forecast`. No arbitrary
host/path or authentication input is accepted. The test double binds only
127.0.0.1 on an ephemeral port. The client disables environment proxies/netrc,
cookies, redirects and automatic decompression in both client and server parsers, checks status/content type and
declared length, then bounds actual streamed bytes independently of headers.
No TLS/remote service deployment is implied by this local test fixture. Envelopes
include contract/admission metadata and immutable reference identities alongside
the approved candle primitives; they contain no raw publications, credentials or
live-feed access. Remote use remains an explicit test authorization.

## Durable lifecycle

`JobStore` owns private `provider-jobs.lock` and `provider-jobs.sqlite3` in the
Installation state directory. A lifetime exclusive flock and inode-pinned SQLite
connection preserve the existing state-file boundary. Schema version 1 is checked
exactly; unknown/corrupt state is rejected before recovery. WAL, synchronous FULL
and explicit BEGIN IMMEDIATE/COMMIT/ROLLBACK protect transitions.

| State | Allowed continuation |
| --- | --- |
| `queued` | Explicit dispatch, durable cancellation or deadline failure |
| `running` | Fenced terminal completion or interruption |
| `interrupted` | Explicit `run(..., resume=True)`, with re-admission and a fresh attempt/token |
| `succeeded` | Read the registered immutable result; rerunning returns the same job |
| `failed`, `cancelled`, `deadline_expired` | Terminal; cannot reset or resume |

The request ID is the logical idempotency key. Exact retries return the stored
job, preserving its first submission timestamp even when clocks advance.
Reusing an ID with different inputs, deadline, seed, policy or review is rejected.
There is no automatic retry or startup execution. Startup turns abandoned running
jobs into interrupted jobs, expires unfinished deadlines and detects UTC rollback.
Queued jobs remain queued until explicit dispatch; deadlines are enforced on
startup and every state access/transition, rather than by a background scheduler.

Active execution uses a monotonic asyncio timeout as well as the persisted UTC
deadline. An early HTTP/provider timeout is `failed/provider_timeout`; an actual
job deadline is `deadline_expired/deadline`. Explicit cancellation is committed
before signaling the child task. Caller cancellation is persisted and propagated,
including when a cooperative provider suppresses its cancellation signal and
returns late data. Graceful `aclose()` commits interruption, cancels active provider
tasks and waits for their cleanup; close the store afterward. Providers in this
profile cooperate with asyncio cancellation. Stopping blocking or hostile code
is an F21 isolation requirement.

## Accepted results and recovery

Results pass application-owned validation and immutable artifact installation
before the conditional success transaction. F20 artifacts additionally bind the
job/review/policy hashes, intended use and declared deterministic seed/device/runtime.
Existing F09 artifact payloads and readers are unchanged.

Cancellation, expiration and stale attempts cannot overwrite a terminal record.
There is at most one accepted success per request ID. A crash after artifact
installation but before success can leave a valid unreferenced object; it is not
visible through `JobService.result()`. Explicit safe resume can reuse that same
immutable object. A crash after success returns the original result without
another execution. Result reads verify the stored object and job admission binding.

This is **at-most-once acceptance**, not exactly-once remote computation. The HTTP
test-double cache deduplicates computation during its lifetime, rewrapping results
with the current attempt fence. Its cache is bounded and in-memory; server restart
may recompute. The application journal remains the authority for acceptance.
There is no automatic history pruning or orphan cleanup; a full job journal fails
closed and requires a separately designed retention policy.

## Application wiring

Trusted code supplies an already reviewed descriptor/model review and a policy
whose exact review, license and use grants were explicitly approved. No code path
manufactures an operator approval simply from provider registration.

```python
from scryntic.forecast.artifact import ImmutableForecastStore
from scryntic.jobs.fake import LocalFakeProvider
from scryntic.jobs.inputs import F18Inputs
from scryntic.jobs.service import JobService
from scryntic.jobs.store import JobStore


# installation, datasets, reviewed_model, approved_policy and request are
# application-owned values. request is the existing ForecastRequest type.
async def forecast_job():
    provider = LocalFakeProvider(reviewed_model, algorithm="persistence")
    with JobStore(installation) as journal:
        jobs = JobService(
            journal,
            (provider,),
            approved_policy,
            ImmutableForecastStore(installation),
            F18Inputs(datasets),
        )
        try:
            jobs.submit(
                request,
                provider.review.descriptor.provider_id,
                intended_use="forecast-research",
                deadline_ns=absolute_deadline_ns,
            )
            await jobs.run(request.request_id)
            return jobs.result(request.request_id)
        finally:
            await jobs.aclose()
```

For explicitly authorized remote fixture tests, use `async with
HttpTestDouble(remote_review)` and register `HttpFakeProvider(remote_review,
double.port)`. Set both remote approvals and retain the same job/response checks.
The integration tests exercise this wiring and the real F18 import/recipe path.

Python 3.12's [SQLite transaction documentation](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)
and [asyncio task documentation](https://docs.python.org/3.12/library/asyncio-task.html#timeouts),
SQLite's [IMMEDIATE transactions](https://www.sqlite.org/lang_transaction.html)
and [FULL synchronization](https://www.sqlite.org/pragma.html#pragma_synchronous),
and aiohttp's [client reference](https://docs.aiohttp.org/en/stable/client_reference.html)
support the implementation decisions. Context7 was used to cross-check consequential
library behavior; SOFA provided a selectively verified second opinion on atomic
claim/rowcount fencing. See [F20 validation evidence](F20_VALIDATION.md).
