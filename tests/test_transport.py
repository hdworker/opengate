import pytest

from opengate.transport import OpenCodeSdkTransport
from opengate.execution import ExecutionService


def test_transport_accepts_docker_host_gateway():
    transport = OpenCodeSdkTransport("http://host.docker.internal:4096")
    assert transport.config.base_url == "http://host.docker.internal:4096"
    assert transport.client.max_retries == 0


def test_transport_rejects_public_backend_url():
    with pytest.raises(ValueError, match="loopback or host.docker.internal"):
        OpenCodeSdkTransport("https://example.com")


def test_execution_service_from_env_prefers_canonical_opengate_settings(monkeypatch):
    monkeypatch.setenv("OPENGATE_URL", "http://host.docker.internal:4096")
    monkeypatch.setenv("OPENGATE_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("OPENGATE_USERNAME", "gateway-user")
    monkeypatch.setenv("OPENGATE_PASSWORD", "gateway-password")
    monkeypatch.setenv("OPENCODE_USERNAME", "legacy-user")
    monkeypatch.setenv("OPENCODE_PASSWORD", "legacy-password")

    service = ExecutionService.from_env(concurrency=1)
    assert service.transport.config.base_url == "http://host.docker.internal:4096"
    assert service.transport.config.timeout == 17.0
    assert service.transport.client.default_headers["Authorization"].startswith("Basic ")
