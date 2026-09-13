from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import asyncio
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
from urllib.parse import urlparse


class ProjectAdapter(Protocol):
    name: str
    instructions: str

    def register_tools(self, mcp: Any, api: "LoopbackAPI") -> None: ...


@dataclass
class LoopbackAPI:
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 30

    def __post_init__(self) -> None:
        if urlparse(self.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Project MCP API must use a loopback URL")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        url = self.base_url.rstrip("/") + path
        if params:
            from urllib.parse import urlencode

            url += "?" + urlencode({key: value for key, value in params.items() if value is not None})
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = Request(url, data=data, method=method, headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(detail or f"Project API returned {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Project API unavailable: {exc}") from exc

    async def acall(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        """Use the stdlib transport without blocking FastMCP's event loop."""
        return await asyncio.to_thread(self.call, method, path, body, params, headers)


def create_mcp_server(adapter: ProjectAdapter, *, api_url: str = "http://127.0.0.1:8000") -> Any:
    """Create a FastMCP server and let the project register its domain tools."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install opengate-mcp[mcp] to run the MCP server") from exc
    server = FastMCP(adapter.name, instructions=adapter.instructions)
    adapter.register_tools(server, LoopbackAPI(api_url))
    return server
