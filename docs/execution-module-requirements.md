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
followed by eligible paid candidates. Runtime speed can reorder only models in
the same preset priority group and only after three successful observations.

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
`model_unavailable`, `gateway_unavailable`, `request_rejected`,
`invalid_response` and `invalid_input`. Classification uses SDK/OpenCode
status, error name, structured data and source message.

- Quota exhaustion moves to the next candidate and records a parsed quota TTL;
  without a usable date the block lasts only for the current Run.
- Model unavailable moves immediately to the next candidate.
- Rate limiting uses one bounded backoff before the next candidate.
- Gateway/timeout failures retry the same model up to two times.
- Rejected requests, invalid responses and invalid input are not retried or
  switched automatically.
- Every retry and model switch creates a new Attempt.

The SDK is created with `max_retries=0`; all routing retries are visible to the
Execution Module. When the full free-plus-paid plan has no remaining candidate,
the Run stops scheduling new Items. Active Items finish within their existing
timeout; queued Items receive `not_started`.

## Batch and persistence boundary

Batch execution uses async tasks and an `asyncio.Semaphore`, default
concurrency 2. Results are yielded as they complete. Input JSONL is immutable;
the old behavior of reading an output file and silently skipping indexes is
removed. There is no SQL store, checkpoint, cross-run resume, cancellation or
mini-web UI in v1. A consumer may persist and explicitly reprocess selected
Items in a later Run.

## Verification

The test suite must pass without a live gateway and cover catalog snapshot
reuse, all six free aliases, preset order, latency maturity, quota TTL,
concurrent quota state, retry classification, diagnostics redaction, session
lifecycle, bounded batch concurrency and plan exhaustion. A separate smoke
test may probe health/version, provider catalog, session create, chat,
messages and cleanup, and must report clearly when OpenCode Serve is absent.
