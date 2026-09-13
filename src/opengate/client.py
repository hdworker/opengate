from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import ModelInfo, choose_models, parse_catalog
from .errors import GatewayError, classify_error
from .jsonl import run_jsonl


@dataclass(frozen=True)
class GatewayResult:
    text: str
    session_id: str
    model: str
    attempts: list[dict[str, Any]]


class OpenGateClient:
    """Small dependency-free client for a loopback OpenCode Serve instance."""

    def __init__(self, base_url: str = "http://127.0.0.1:4096", *, username: str = "", password: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    @classmethod
    def from_env(cls) -> "OpenGateClient":
        import os

        return cls(
            os.getenv("OPENGATE_URL", "http://127.0.0.1:4096"),
            username=os.getenv("OPENCODE_SERVER_USERNAME", ""),
            password=os.getenv("OPENCODE_SERVER_PASSWORD", ""),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _request(self, path: str, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise GatewayError(raw or str(exc), kind=classify_error(exc.code, raw), status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayError(str(exc), kind="gateway_unavailable") from exc
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    def ensure_available(self, timeout: float = 20) -> dict[str, Any]:
        """Probe only; process startup is deliberately owned by the CLI/bootstrap."""
        payload = self._request("/config/providers", timeout=min(timeout, 5))
        return {"status": "online", "url": self.base_url, "providers": len(payload.get("providers", [])) if isinstance(payload, dict) else 0}

    def catalog(self, timeout: float = 10) -> list[ModelInfo]:
        return parse_catalog(self._request("/config/providers", timeout=timeout))

    def session_messages(self, session_id: str, *, directory: str = "", timeout: float = 10) -> list[dict[str, Any]]:
        suffix = "?directory=" + urllib.parse.quote(directory) if directory else ""
        payload = self._request(f"/session/{session_id}/message{suffix}", timeout=timeout)
        return payload if isinstance(payload, list) else []

    def _wait_for_answer(self, session_id: str, *, directory: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                messages = self.session_messages(session_id, directory=directory, timeout=min(10, max(1, deadline - time.monotonic())))
            except GatewayError as exc:
                if exc.kind == "gateway_unavailable" and time.monotonic() < deadline:
                    last_error = str(exc)
                    time.sleep(0.4)
                    continue
                raise GatewayError(str(exc), kind=exc.kind, status=exc.status, session_id=session_id) from exc
            for message in reversed(messages):
                info = message.get("info") or {}
                if info.get("role") != "assistant":
                    continue
                parts = message.get("parts") or []
                text = "\n\n".join(str(part.get("text", "")).strip() for part in parts if isinstance(part, dict) and part.get("type") == "text" and part.get("text"))
                if text:
                    return text
                for part in parts:
                    if isinstance(part, dict) and (part.get("error") or part.get("type") == "error"):
                        last_error = str(part.get("error") or part.get("message") or part)
                if info.get("error"):
                    last_error = str(info["error"])
            if last_error and "timed out" not in last_error.casefold():
                raise GatewayError(last_error, session_id=session_id)
            time.sleep(0.4)
        raise GatewayError("OpenGate generation timed out", kind="gateway_unavailable", session_id=session_id)

    def run(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "",
        task: str = "",
        directory: str = "",
        timeout: float = 180,
        keep_session: bool = False,
        session_title: str = "OpenGate request",
    ) -> GatewayResult:
        suffix = "?directory=" + urllib.parse.quote(directory) if directory else ""
        candidates = choose_models(self.catalog(min(timeout, 10)), task) if task else []
        candidates = ([model] + [item for item in candidates if item != model]) if model and model != "opencode/default" else candidates or ["opencode/default"]
        attempts: list[dict[str, Any]] = []
        for candidate in candidates:
            session_id = ""
            try:
                session = self._request(f"/session{suffix}", "POST", {"title": session_title}, timeout)
                session_id = str(session.get("id", ""))
                if not session_id:
                    raise GatewayError("OpenCode did not return a session id", kind="invalid_response")
                body: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
                if system:
                    body["system"] = system
                if candidate != "opencode/default":
                    provider, separator, model_id = candidate.partition("/")
                    if not separator:
                        raise ValueError("model must have the form provider/model")
                    body["model"] = {"providerID": provider, "modelID": model_id}
                self._request(f"/session/{session_id}/message{suffix}", "POST", body, timeout)
                text = self._wait_for_answer(session_id, directory=directory, timeout=timeout)
                attempts.append({"session_id": session_id, "model": candidate, "status": "succeeded"})
                return GatewayResult(text, session_id, candidate, attempts)
            except GatewayError as exc:
                attempts.append({"session_id": session_id or exc.session_id, "model": candidate, "status": "failed", "error_kind": exc.kind, "error": str(exc)[:1000]})
                if exc.kind not in {"rate_limit", "model_unavailable"} or candidate == candidates[-1]:
                    raise GatewayError(str(exc), kind=exc.kind, status=exc.status, session_id=session_id, attempts=attempts) from exc
            finally:
                if session_id and not keep_session:
                    try:
                        self._request(f"/session/{session_id}{suffix}", "DELETE", timeout=timeout)
                    except GatewayError:
                        pass
        raise GatewayError("No eligible OpenGate model succeeded", kind="model_unavailable", attempts=attempts)

    def run_batch(
        self,
        input_path: Path,
        output_path: Path,
        *,
        field: str = "text",
        template: str = "{text}",
        task: str = "",
        model: str = "",
        workers: int = 2,
        timeout: float = 180,
        keep_sessions: bool = False,
        session_title: str = "OpenGate batch request",
    ) -> int:
        """Run a bounded, resumable JSONL batch through this client."""
        def process(record: dict[str, Any]) -> dict[str, Any]:
            try:
                value = record.get(field, "")
                prompt = template.format_map({**record, "value": value})
                result = self.run(prompt, task=task, model=model, timeout=timeout, keep_session=keep_sessions, session_title=session_title)
                return {"ok": True, "output": result.text, "session_id": result.session_id, "model": result.model, "attempts": result.attempts}
            except GatewayError as exc:
                return {"ok": False, "error": {"kind": exc.kind, "status": exc.status, "message": str(exc), "session_id": exc.session_id, "attempts": exc.attempts}}

        return run_jsonl(input_path, output_path, process, workers=workers)
