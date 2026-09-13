# OpenGate MCP

Reusable local integration for projects that need an OpenAI-compatible model
provider through OpenCode Serve and an agent-facing MCP server.

The package keeps the boundary explicit:

```text
Agent -> MCP stdio -> project loopback API -> project DB/worker -> OpenGate
                                                       \-> OpenCode Serve
```

OpenGate is local and must remain bound to `127.0.0.1`. The MCP runtime never
opens PostgreSQL or project files directly. A project supplies an adapter that
registers domain tools and decides which read models and mutations are safe.

## Install

```powershell
uv pip install -e ".[mcp,dev]"
```

## Check and use OpenGate

```powershell
opengate ensure
opengate models
opengate prompt "Return JSON with three facts" --task fast-extraction
opengate init C:\Code\new-project --name "New Project"
```

The live model catalog is queried before selecting a task preset. No hosted
OpenAI fallback is performed by this package.

## Project MCP adapter

```python
from opengate.mcp_runtime import LoopbackAPI, create_mcp_server


class ProjectAdapter:
    name = "My Project"
    instructions = "Use project tools through the local API."

    def register_tools(self, mcp, api: LoopbackAPI):
        @mcp.tool()
        async def project_get_context() -> dict:
            """Read the project's safe analysis context."""
            return api.call("GET", "/api/internal/mcp/context")


if __name__ == "__main__":
    create_mcp_server(ProjectAdapter()).run("stdio")
```

See [docs/protocol.md](docs/protocol.md) for the adapter and task contract.
