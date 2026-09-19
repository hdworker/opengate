"""Compatibility import surface for the breaking Execution Service API."""

from .execution import (
    Attempt,
    BatchItem,
    ExecutionRequest,
    ExecutionResult,
    ExecutionService,
    HealthRegistry,
    ItemResult,
    SessionHandle,
)
from .transport import OpenCodeSdkTransport


class OpenGateClient(ExecutionService):
    """Compatibility constructor; the old synchronous methods are removed."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        *,
        username: str = "",
        password: str = "",
        concurrency: int = 2,
        readiness_retries: int = 3,
        readiness_backoff: float = 1.0,
    ) -> None:
        super().__init__(
            OpenCodeSdkTransport(base_url, username=username, password=password),
            concurrency=concurrency,
            readiness_retries=readiness_retries,
            readiness_backoff=readiness_backoff,
        )

    @classmethod
    def from_env(cls) -> "OpenGateClient":
        import os

        return cls(
            os.getenv("OPENGATE_URL", "http://127.0.0.1:4096"),
            username=os.getenv("OPENGATE_USERNAME", os.getenv("OPENCODE_USERNAME", os.getenv("OPENCODE_SERVER_USERNAME", ""))),
            password=os.getenv("OPENGATE_PASSWORD", os.getenv("OPENCODE_PASSWORD", os.getenv("OPENCODE_SERVER_PASSWORD", ""))),
            readiness_retries=int(os.getenv("OPENGATE_READINESS_RETRIES", "3")),
            readiness_backoff=float(os.getenv("OPENGATE_READINESS_BACKOFF_SECONDS", "1")),
        )

__all__ = [
    "Attempt",
    "BatchItem",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionService",
    "HealthRegistry",
    "ItemResult",
    "OpenCodeSdkTransport",
    "OpenGateClient",
    "SessionHandle",
]
