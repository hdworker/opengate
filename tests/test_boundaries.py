import pytest

from opengate.bootstrap import ensure_server
from opengate.client import OpenGateClient
from opengate.mcp_runtime import LoopbackAPI


@pytest.mark.parametrize("factory", [OpenGateClient, LoopbackAPI])
def test_external_urls_are_rejected(factory):
    with pytest.raises(ValueError, match="loopback"):
        factory("http://example.com:8000")


def test_bootstrap_rejects_external_bind_address():
    with pytest.raises(ValueError, match="loopback"):
        ensure_server("0.0.0.0", no_start=True)

