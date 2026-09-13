from __future__ import annotations


class GatewayError(RuntimeError):
    """A classified error returned by the local OpenGate/OpenCode gateway."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "gateway_error",
        status: int | None = None,
        session_id: str = "",
        attempts: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.session_id = session_id
        self.attempts = attempts or []


def classify_error(status: int | None, message: str) -> str:
    text = message.casefold()
    if status in {402, 429} or any(
        token in text for token in ("rate limit", "rate_limit", "quota", "credit", "limit exceeded")
    ):
        return "rate_limit"
    if status in {400, 404, 410, 422} and any(
        token in text for token in ("model", "provider", "not found", "unavailable", "unsupported")
    ):
        return "model_unavailable"
    return "gateway_unavailable" if status is None else "request_failed"

