# Migration map

The first consumers are `C:\Code\tg-speech` and
`C:\Code\alice-tour-client`. Their domain code remains in place; only shared
provider and MCP mechanics move to this repository.

| Current code | New owner | Migration rule |
| --- | --- | --- |
| `tg-speech/backend/services/monitor_analysis.py` OpenGate subprocess client | `opengate.client.OpenGateClient` + `opengate.jsonl` | Keep monitor prompts, safe SQL planning and validation in Efir |
| `alice-tour-client/server/reviews.py` OpenGate subprocess client | `opengate.client.OpenGateClient` | Keep tour/review prompt and result validation in AliceTour |
| `tg-speech/mcp/efir_server.py` manual JSON-RPC loop | `opengate.mcp_runtime.create_mcp_server` | Register Efir tools in a thin project adapter |
| `alice-tour-client/server/mcp_server.py` FastMCP setup and HTTP helper | `opengate.mcp_runtime` | Keep only AliceTour tool definitions and endpoint paths |
| `tg-speech/backend/services/mcp_task_service.py` | Efir project | Keep domain task processing; optionally implement the task-kernel protocol later |
| `alice-tour-client/server/task_service.py` | AliceTour project | Keep review collection and approval semantics |

The migration is intentionally staged. Each project first installs the new
package and runs its adapter against a mocked loopback API. Only then is the
old OpenGate subprocess code removed. This avoids coupling a project release
to a new package release and keeps rollback possible.

