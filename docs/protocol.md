# Project integration protocol v0

The reusable repository owns transport and provider mechanics. A project owns
its domain, database, authorization policy, durable task records, prompts and
result validation.

## Loopback API

The MCP process may call only an explicitly configured loopback URL. Typical
project endpoints are:

- `GET /api/internal/mcp/context` — bounded, safe read model for analysis;
- `POST /api/internal/mcp/tasks` — create an idempotent durable task with an
  `operation`, human instruction and structured `parameters` passed to the
  project's worker;
- `GET /api/internal/mcp/tasks/{id}` — status and progress;
- `GET /api/internal/mcp/tasks/{id}/result` — validated result;
- `POST /api/internal/mcp/tasks/{id}/cancel` — request cancellation;
- `POST /api/internal/mcp/tasks/{id}/approve` — explicit publication/approval.

These paths are examples, not a database contract. Each project may expose a
smaller surface. The API must reject non-loopback deployment and must never
return credentials, Telegram sessions, or arbitrary SQL access.

The reusable MCP scaffold deliberately sends OpenGate parameters to the project
API, not directly to OpenGate. This keeps prompts, model policy, source data,
validation and durable state under project control.

## OpenGate execution envelope

Project workers call the async Python API with an `ExecutionRequest`:

```python
result = await service.execute(
    ExecutionRequest(prompt, task="fast-extraction", billing_mode="free-first")
)
```

`ExecutionResult` contains text, final model, optional `SessionHandle`, ordered
Attempts, TTFT, total latency and the Run's catalog snapshot. A new OpenCode
session is created for every Attempt unless the caller explicitly supplies a
`SessionHandle` with `session_mode="continue"`.

For batches, `service.execute_batch(request, items)` yields completed
`ItemResult` values through an async iterator. JSONL input is immutable and an
existing output file is not interpreted as a checkpoint. A consumer may write
results as they arrive, but cross-run resume and checkpoint storage are not v1
features.

## Status semantics

The project task remains durable and authoritative. OpenGate session IDs are
diagnostic/provider metadata, not the project task identity. Cancellation and
approval are project decisions; the provider client only reports gateway
operation state and does not publish project results.
