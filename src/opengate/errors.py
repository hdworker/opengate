from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ERROR_KINDS = {
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "gateway_unavailable",
    "request_rejected",
    "invalid_response",
    "invalid_input",
}


@dataclass(frozen=True)
class ErrorDiagnostic:
    """Bounded provider evidence retained for routing and debugging."""

    stage: str
    message: str
    status: int | None = None
    source_name: str = ""
    source_data: Any = None
    model: str = ""
    provider: str = ""


class ExecutionError(RuntimeError):
    """A classified failure of one execution attempt or Run."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "gateway_unavailable",
        diagnostic: ErrorDiagnostic | None = None,
        attempts: list[Any] | None = None,
        session_id: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "gateway_unavailable"
        self.diagnostic = diagnostic
        self.attempts = attempts or []
        self.session_id = session_id

    @property
    def status(self) -> int | None:
        return self.diagnostic.status if self.diagnostic else None


GatewayError = ExecutionError


def classify_error(status: int | None, message: str, *, source_name: str = "") -> str:
    """Classify OpenCode evidence without collapsing quota and rate limit."""
    text = f"{source_name} {message}".casefold()
    if any(token in text for token in ("quota", "credit exhausted", "credits exhausted", "limit exceeded", "exhausted", "insufficient credit")):
        return "quota_exhausted"
    if status == 429 or any(token in text for token in ("rate limit", "rate_limit", "too many requests")):
        return "rate_limited"
    if status in {400, 404, 410, 422} and any(token in text for token in ("model", "provider", "not found", "unavailable", "unsupported")):
        return "model_unavailable"
    if status is None or status in {408, 409} or status >= 500:
        return "gateway_unavailable"
    return "request_rejected"
