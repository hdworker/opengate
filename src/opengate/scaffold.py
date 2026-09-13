from __future__ import annotations

import json
from pathlib import Path


def render_adapter(module_name: str, project_name: str) -> str:
    return f'''from opengate.mcp_runtime import LoopbackAPI, create_mcp_server


class ProjectAdapter:
    name = {project_name!r}
    instructions = "Read project context through the provided tools; submit mutations for explicit approval."

    def register_tools(self, mcp, api: LoopbackAPI) -> None:
        @mcp.tool()
        async def project_get_context() -> dict:
            """Return the bounded read model exposed by the project API."""
            return await api.acall("GET", "/api/internal/mcp/context")

        @mcp.tool()
        async def project_create_task(instruction: str, idempotency_key: str) -> dict:
            """Create a durable project task that may call OpenGate."""
            if not instruction.strip() or not idempotency_key.strip():
                raise ValueError("instruction and idempotency_key are required")
            return await api.acall("POST", "/api/internal/mcp/tasks", {{
                "instruction": instruction,
                "idempotency_key": idempotency_key,
            }})

        @mcp.tool()
        async def project_get_task(task_id: int) -> dict:
            """Read task status and progress."""
            return await api.acall("GET", f"/api/internal/mcp/tasks/{{task_id}}")


if __name__ == "__main__":
    import os

    create_mcp_server(ProjectAdapter(), api_url=os.getenv("PROJECT_INTERNAL_API_URL", "http://127.0.0.1:8000")).run("stdio")
'''


def init_project(target: Path, project_name: str, *, force: bool = False) -> list[Path]:
    """Create the smallest explicit project integration, never overwriting by default."""
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    module_name = "project_mcp.py"
    files = {
        module_name: render_adapter(module_name, project_name),
        ".env.opengate.example": "OPENGATE_URL=http://127.0.0.1:4096\nPROJECT_INTERNAL_API_URL=http://127.0.0.1:8000\n",
        "mcp-config.example.json": json.dumps(
            {
                "mcpServers": {
                    project_name.lower().replace(" ", "-"): {
                        "command": "python",
                        "args": [module_name],
                        "env": {"PROJECT_INTERNAL_API_URL": "http://127.0.0.1:8000"},
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    written: list[Path] = []
    for name, content in files.items():
        path = target / name
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite {path}; pass force=True to replace it")
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written
