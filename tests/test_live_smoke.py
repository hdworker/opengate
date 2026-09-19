import os

import pytest

from opengate import ExecutionRequest, ExecutionService
from opengate.errors import ExecutionError


@pytest.mark.skipif(os.getenv("OPENGATE_RUN_LIVE") != "1", reason="set OPENGATE_RUN_LIVE=1 to probe a running OpenCode Serve")
def test_live_opencode_sdk_smoke():
    async def run():
        service = ExecutionService.from_env()
        try:
            health = await service.transport.health()
            assert health["status"] == "ok"
            snapshot = await service.catalog_snapshot()
            assert snapshot.models
            result = await service.execute(ExecutionRequest("Return exactly OK.", model="opencode/big-pickle", billing_mode="strict-model", timeout=30))
            assert result.text
            assert result.attempts
        finally:
            await service.close()

    import asyncio

    try:
        asyncio.run(run())
    except ExecutionError as exc:
        if exc.kind == "gateway_unavailable":
            pytest.skip(f"OpenCode Serve unavailable: {exc}")
        raise
