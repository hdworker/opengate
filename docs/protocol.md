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

## OpenGate envelope

Project workers call `OpenGateClient.run()` with a prompt, optional system
instruction, explicit model or live-catalog task preset, and a stable session
title. Results include `text`, `session_id`, `model`, and an ordered attempt
log. Long operations use `opengate.jsonl.run_jsonl()`; output records contain
the input index and are safe to resume.

## Status semantics

The project task remains durable and authoritative. OpenGate session IDs are
diagnostic/provider metadata, not the project task identity. Cancellation and
approval are project decisions; the provider client only reports gateway
operation state and does not publish project results.
