from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from opencode_ai import AsyncOpencode

from .errors import ErrorDiagnostic, ExecutionError, classify_error, sanitize_diagnostic


class ExecutionTransport(Protocol):
    """Transport seam whose adapters translate external failures to ExecutionError.

    Raw exceptions escaping an adapter call indicate an implementation failure;
    callers must not classify them as transient gateway failures.
    """

    async def providers(self, *, directory: str = "") -> Any: ...

    async def health(self) -> dict[str, Any]: ...

    async def create_session(self, *, title: str, directory: str = "") -> Any: ...

    async def chat(
        self,
        session_id: str,
        *,
        prompt: str,
        provider: str,
        model: str,
        system: str = "",
        directory: str = "",
        agent: str = "",
        variant: str = "",
    ) -> Any: ...

    async def messages(self, session_id: str, *, directory: str = "") -> Any: ...

    async def delete_session(self, session_id: str, *, directory: str = "") -> Any: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class TransportConfig:
    base_url: str = "http://127.0.0.1:4096"
    timeout: float = 180.0


def _bounded(value: Any, limit: int = 2_000) -> Any:
    """Backward-compatible alias for the shared diagnostic sanitizer."""
    return sanitize_diagnostic(value, max_string=limit)


class OpenCodeSdkTransport:
    """Thin async adapter over the official OpenCode Python SDK.

    The adapter connects to an already running OpenCode Serve on loopback or the
    Docker host gateway. It does
    not start a process and deliberately disables SDK retries so the execution
    domain can record every attempt and model switch.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        *,
        timeout: float = 180.0,
        username: str = "",
        password: str = "",
    ) -> None:
        parsed = urlparse(base_url)
        allowed_hosts = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
            raise ValueError("OpenGate SDK transport only permits loopback or host.docker.internal base URLs")
        self.config = TransportConfig(base_url=base_url.rstrip("/"), timeout=timeout)
        headers: dict[str, str] = {}
        if username or password:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        self.client = AsyncOpencode(
            base_url=self.config.base_url,
            timeout=timeout,
            max_retries=0,
            default_headers=headers or None,
        )

    @staticmethod
    def _query(directory: str) -> dict[str, str]:
        return {"directory": directory} if directory else {}

    def _failure(self, exc: Exception, *, stage: str, model: str = "", sensitive_values: tuple[str, ...] = ()) -> ExecutionError:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        body = getattr(exc, "body", None)
        if body is None and response is not None:
            try:
                body = response.json()
            except Exception:
                body = getattr(response, "text", None)
        message = str(exc) or exc.__class__.__name__
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("error") or message)
        elif isinstance(body, str) and body:
            message = body[:2_000]
        message = sanitize_diagnostic(message, sensitive_values=sensitive_values)
        diagnostic = ErrorDiagnostic(
            stage=stage,
            message=message[:2_000],
            status=int(status) if isinstance(status, int) else None,
            source_name=exc.__class__.__name__,
            source_data=sanitize_diagnostic(body if body is not None else {"message": message}, sensitive_values=sensitive_values),
            model=model,
        )
        return ExecutionError(message, kind=classify_error(diagnostic.status, message, source_name=diagnostic.source_name), diagnostic=diagnostic)

    async def providers(self, *, directory: str = "") -> Any:
        try:
            return await self.client.app.providers(extra_query=self._query(directory), timeout=self.config.timeout)
        except Exception as exc:
            raise self._failure(exc, stage="catalog") from exc

    async def health(self) -> dict[str, Any]:
        try:
            response = await self.client.get("/global/health", cast_to=httpx.Response)
            data: Any
            try:
                data = response.json()
            except Exception:
                data = {"text": response.text}
            return {"status": "ok", "data": _bounded(data)}
        except Exception as exc:
            raise self._failure(exc, stage="health") from exc

    async def create_session(self, *, title: str, directory: str = "") -> Any:
        try:
            return await self.client.session.create(
                extra_body={"title": title},
                extra_query=self._query(directory),
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise self._failure(exc, stage="session_create") from exc

    async def chat(
        self,
        session_id: str,
        *,
        prompt: str,
        provider: str,
        model: str,
        system: str = "",
        directory: str = "",
        agent: str = "",
        variant: str = "",
    ) -> Any:
        try:
            extra_body = {
                key: value
                for key, value in {"agent": agent, "variant": variant}.items()
                if value
            }
            return await self.client.session.chat(
                session_id,
                provider_id=provider,
                model_id=model,
                parts=[{"type": "text", "text": prompt}],
                system=system,
                extra_body=extra_body or None,
                extra_query=self._query(directory),
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise self._failure(exc, stage="message_submit", model=f"{provider}/{model}", sensitive_values=(prompt, system)) from exc

    async def messages(self, session_id: str, *, directory: str = "") -> Any:
        try:
            return await self.client.session.messages(
                session_id,
                extra_query=self._query(directory),
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise self._failure(exc, stage="answer_poll") from exc

    async def delete_session(self, session_id: str, *, directory: str = "") -> Any:
        try:
            return await self.client.session.delete(session_id, extra_query=self._query(directory), timeout=self.config.timeout)
        except Exception as exc:
            raise self._failure(exc, stage="session_delete") from exc

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> "OpenCodeSdkTransport":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
