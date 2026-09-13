"""Reusable local OpenGate and project-MCP components."""

from .client import GatewayResult, OpenGateClient
from .errors import GatewayError

__all__ = ["GatewayError", "GatewayResult", "OpenGateClient"]

