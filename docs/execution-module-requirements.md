# OpenGate Execution Module Requirements

Status: v1 implementation baseline.

The Execution Module provides reusable async access to an already running
loopback OpenCode Serve. It owns provider transport, catalog planning, model
rotation, Attempt diagnostics and in-memory batch scheduling. Projects retain
their prompts, source records, worker logic, validation and durable task state.

## Public API

```python
result = await service.execute(ExecutionRequest(prompt, task="deep-analysis"))

async for item in service.execute_batch(request, items):
    save(item)
```

`ExecutionRequest` carries prompt, system instruction, preset/model policy,
timeout, directory and explicit session continuation. `ExecutionResult` carries
text, final model, optional `SessionHandle`, ordered Attempts, TTFT, total
latency and the immutable catalog snapshot. `ItemResult` distinguishes
`succeeded`, `failed` and `not_started`.

Structured output supports the keywords `type`, `enum`, `required`,
`properties`, `items`, and `additionalProperties`, including boolean
subschemas. `enum` comparison follows JSON types: `true` differs from `1`,
while numeric `1` and `1.0` compare equal.

The Python API is the primary interface. The CLI is a thin async wrapper; MCP
remains a project integration layer and is not the execution API.

## Run, Item and Attempt

Each execution Run captures exactly one `CatalogSnapshot` before processing
Items. The snapshot is reused for every Item and the catalog is never queried
per record. A new OpenCode session is created for every Attempt by default.
Only an explicit `SessionHandle` with `session_mode="continue"` reuses a
session.

An Attempt records provider/model, session, stage, timestamps, TTFT, total
latency and bounded diagnostic evidence. Credentials and full prompts are
never retained.

## Candidate planning

Presets filter by context, reasoning and attachment capability and define the
primary order. Candidate plans are partitioned into eligible free candidates
followed by eligible paid candidates; health and latency ordering never moves
a paid candidate ahead of a free one. Within that billing order, preset
priority remains primary, transport health is preferred within a priority
group, and runtime speed can reorder candidates only within that group and
only after three successful observations. Ties retain their input order.
`health_snapshot()` exposes `transport_healthy` as endpoint reachability, not
model eligibility: a successful invocation or HTTP status 100–499 means
reachable; HTTP status 500 or higher and a status-less transport invocation
failure mean unhealthy. A status-less local validation or model-response error
preserves the previous health signal because it does not establish transport
failure.

The six known free identities are:

- `opencode/big-pickle`
- `opencode/mimo-v2.5-free`
- `opencode/ling-3.0-flash-fin-free`
- `opencode/nemotron-3-ultra-free`
- `opencode/nemotron-3.5-lightning-free`
- `opencode/muse-spark-1.3-contributor-free`

Catalog information is advisory. A missing known identity remains eligible for
a real trial; an explicitly catalogued incompatible model is filtered out.
The live invocation is the final availability check.

Supported resolved policies are `free-first`, `free-only`, `paid-only` and
`strict-model`. A project/MCP agent resolves contextual user intent and passes
the policy explicitly; incidental prompt text is not parsed as a billing rule.

## Diagnostics and retry policy

Normalized error kinds are `quota_exhausted`, `rate_limited`,
`model_unavailable`, `gateway_unavailable`, `plan_exhausted`, `internal_error`,
`request_rejected`, `invalid_response` and `invalid_input`. Classification uses
SDK/OpenCode status, error name, structured data and source message. A
`plan_exhausted` result means the Run has no usable candidate left; it is not a
provider quota report.

Planning failures stay within the typed `ExecutionError` API: an unknown task
preset is `invalid_input`, an empty eligible candidate pool is
`model_unavailable`, and malformed catalog/planner data is `invalid_response`.

- Quota exhaustion moves to the next candidate and records a parsed quota TTL;
  without a usable date the block lasts only for the current Run.
- Model unavailable moves immediately to the next candidate.
- Rate limiting uses one bounded backoff before the next candidate.
- `ExecutionTransport` adapters must translate external/backend failures into
  typed `ExecutionError` values. Only an adapter-raised
  `ExecutionError(kind="gateway_unavailable")` during invocation and an
  ExecutionService timeout retry the same model up to two times. An unexpected
  raw adapter exception is an `internal_error` and is neither retried nor
  routed to another model.
- Rejected requests, invalid responses and invalid input are not retried or
  switched automatically.
- Every retry and model switch creates a new Attempt.

The SDK is created with `max_retries=0`; all routing retries are visible to the
Execution Module. When the full free-plus-paid plan has no remaining candidate,
the Run stops scheduling new Items. Active Items finish within their existing
timeout; queued Items receive `outcome="not_started"` with
`kind="plan_exhausted"`.

## Batch and persistence boundary

Batch execution consumes input lazily and keeps at most the configured number
of item tasks in flight, default concurrency 2. Results are yielded as they
complete. Input JSONL is immutable;
the old behavior of reading an output file and silently skipping indexes is
removed. There is no SQL store, checkpoint, cross-run resume or mini-web UI in
v1. If a consumer closes or cancels the async iterator, unfinished local worker
tasks are cancelled, including active calls. Plan exhaustion is different: it
stops scheduling new Items but allows active Attempts to finish within their
existing timeout. A consumer may persist and explicitly reprocess selected
Items in a later Run.

## Verification

The test suite must pass without a live gateway and cover catalog snapshot
reuse, all six free aliases, preset order, latency maturity, quota TTL,
concurrent quota state, retry classification, diagnostics redaction, session
lifecycle, bounded batch concurrency and plan exhaustion. A separate smoke
test may probe health/version, provider catalog, session create, chat,
messages and cleanup, and must report clearly when OpenCode Serve is absent.
