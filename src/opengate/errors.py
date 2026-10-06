from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|authorization|credential|access[_-]?key|private[_-]?key)", re.I)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"),
    re.compile(r"(?i)([\"']?\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|credential)\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}&]+)([\"']?)"),
)


def sanitize_diagnostic(value: Any, *, sensitive_values: tuple[str, ...] = (), max_depth: int = 6, max_string: int = 2_000) -> Any:
    """Bound provider evidence and remove common credential/prompt material."""
    def clean_string(text: str) -> str:
        for secret in sensitive_values:
            if isinstance(secret, str) and secret:
                text = text.replace(secret, "[redacted]")
        for pattern in _SECRET_PATTERNS:
            if pattern.groups == 3:
                text = pattern.sub(lambda match: f"{match.group(1)}[redacted]{match.group(3)}", text)
            else:
                text = pattern.sub("[redacted]", text)
        return text[:max_string]

    def clean(item: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[truncated]"
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= 50:
                    result["[truncated]"] = "additional fields omitted"
                    break
                key_text = clean_string(str(key))[:200]
                result[key_text] = "[redacted]" if _SECRET_KEY.search(str(key)) else clean(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            result = [clean(child, depth + 1) for child in item[:20]]
            if len(item) > 20:
                result.append("[truncated]")
            return result
        if isinstance(item, str):
            return clean_string(item)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return clean_string(str(item))

    return clean(value, 0)


ERROR_KINDS = {
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "gateway_unavailable",
    "request_rejected",
    "invalid_response",
    "invalid_input",
    "plan_exhausted",
    "internal_error",
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", str(sanitize_diagnostic(str(self.stage))))
        object.__setattr__(self, "message", sanitize_diagnostic(str(self.message)))
        object.__setattr__(self, "source_data", sanitize_diagnostic(self.source_data))
        object.__setattr__(self, "source_name", str(sanitize_diagnostic(str(self.source_name))))
        object.__setattr__(self, "model", str(sanitize_diagnostic(str(self.model))))
        object.__setattr__(self, "provider", str(sanitize_diagnostic(str(self.provider))))


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
    # OpenCode/Zen may return a provider-level 403 when the selected model is
    # forbidden for the current account. This is a candidate failure, not a
    # gateway outage: retrying the same model only multiplies latency and can
    # make a large batch look hung.
    if "model" in text and (status == 403 or "forbidden" in text):
        return "model_unavailable"
    if status in {400, 404, 410, 422} and any(token in text for token in ("model", "provider", "not found", "unavailable", "unsupported")):
        return "model_unavailable"
    if status is None or status in {408, 409} or status >= 500:
        return "gateway_unavailable"
    return "request_rejected"
