# Migration map

The first consumers are `C:\Code\tg-speech` and
`C:\Code\alice-tour-client`. Their domain code remains in place; only shared
provider and MCP mechanics move to this repository.

| Current code | New owner | Migration rule |
| --- | --- | --- |
| `tg-speech/backend/services/monitor_analysis.py` OpenGate subprocess client | `opengate.ExecutionService` | Keep monitor prompts, safe SQL planning and validation in Efir |
| `alice-tour-client/server/reviews.py` OpenGate subprocess client | `opengate.ExecutionService` | Keep tour/review prompt and result validation in AliceTour |
| `tg-speech/mcp/efir_server.py` manual JSON-RPC loop | `opengate.mcp_runtime.create_mcp_server` | Register Efir tools in a thin project adapter |
| `alice-tour-client/server/mcp_server.py` FastMCP setup and HTTP helper | `opengate.mcp_runtime` | Keep only AliceTour tool definitions and endpoint paths |
| `tg-speech/backend/services/mcp_task_service.py` | Efir project | Keep domain task processing; optionally implement the task-kernel protocol later |
| `alice-tour-client/server/task_service.py` | AliceTour project | Keep review collection and approval semantics |

The migration is intentionally staged. Each project first installs the package
with `uv pip install -e "C:\Code\opengate[mcp,dev]"`, runs the service against
a fake transport and then performs an explicit OpenCode Serve smoke test. Only
then is the old subprocess code removed.

The v1 batch API has no durable checkpoint or automatic cross-run resume. If a
consumer needs recovery, it must persist `ItemResult` values and explicitly
choose which inputs to submit to a later Run.

Efir currently exposes the first migration switch as `OPENGATE_USE_PACKAGE=1`.
It routes observation batches, synthesis and operation planning through the
new client while retaining the old scripts when the switch is `0`.
