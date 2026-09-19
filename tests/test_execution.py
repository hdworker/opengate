import asyncio
from datetime import datetime, timezone

from opengate.catalog import FREE_MODEL_KEYS
from opengate.errors import ErrorDiagnostic, ExecutionError, classify_error
from opengate.execution import BatchItem, ExecutionRequest, ExecutionService, HealthRegistry, SessionHandle, _quota_expiry


class FakeTransport:
    def __init__(self, *, failures=None, delay=0.0, fail_all=False):
        self.failures = failures or {}
        self.delay = delay
        self.fail_all = fail_all
        self.provider_calls = 0
        self.created = []
        self.deleted = []
        self.chat_options = []
        self.active = 0
        self.max_active = 0

    async def providers(self, *, directory=""):
        self.provider_calls += 1
        return {"providers": [{"id": "opencode", "models": {
            "big-pickle": {"name": "Big Pickle", "limit": {"context": 200000}, "cost": {"input": 0, "output": 0}},
            "paid": {"name": "Paid", "limit": {"context": 200000}, "cost": {"input": 1, "output": 1}},
        }}]}

    async def health(self):
        return {"status": "ok"}

    async def create_session(self, *, title, directory=""):
        session_id = f"s{len(self.created) + 1}"
        self.created.append(session_id)
        return {"id": session_id}

    async def chat(self, session_id, *, prompt, provider, model, system="", directory="", agent="", variant=""):
        self.chat_options.append({"agent": agent, "variant": variant})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.active -= 1
        failure = self.failures.get(f"{provider}/{model}")
        if self.fail_all:
            failure = error("model_unavailable", "model unavailable", 404)
        if failure:
            raise failure() if callable(failure) else failure
        return {"role": "assistant"}

    async def messages(self, session_id, *, directory=""):
        return [{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "ok"}]}]

    async def delete_session(self, session_id, *, directory=""):
        self.deleted.append(session_id)

    async def close(self):
        return None


def error(kind, message, status=None):
    return ExecutionError(message, kind=kind, diagnostic=ErrorDiagnostic(stage="chat", message=message, status=status))


def test_six_free_aliases_are_known():
    assert len(FREE_MODEL_KEYS) == 6


def test_error_classification_uses_status_and_text():
    assert classify_error(402, "payment required") == "request_rejected"
    assert classify_error(402, "free quota exhausted") == "quota_exhausted"
    assert classify_error(429, "too many requests") == "rate_limited"


def test_quota_registry_expires():
    async def run():
        registry = HealthRegistry()
        now = datetime.now(timezone.utc)
        await registry.record_error("m", ErrorDiagnostic("chat", "quota"), quota_until=now)
        assert not await registry.is_blocked("m", now=now)
        future = now.replace(year=now.year + 1)
        await registry.record_error("m", ErrorDiagnostic("chat", "quota"), quota_until=future)
        assert await registry.is_blocked("m", now=now)
        assert not await registry.is_blocked("m", now=future)
        assert _quota_expiry("retry in 5 minutes", now=now) is not None
    asyncio.run(run())


def test_execute_snapshots_catalog_once_and_creates_attempt_session():
    async def run():
        transport = FakeTransport()
        service = ExecutionService(transport, backoff_base=0)
        result = await service.execute(ExecutionRequest("hello", model="opencode/big-pickle", billing_mode="strict-model"))
        assert transport.provider_calls == 1
        assert result.text == "ok"
        assert result.model == "opencode/big-pickle"
        assert result.attempts[0].session_id == "s1"
        assert transport.deleted == ["s1"]
    asyncio.run(run())


def test_execute_waits_for_gateway_readiness_before_creating_attempt():
    async def run():
        transport = FakeTransport()
        original_providers = transport.providers
        probe_count = 0

        async def flaky_providers(*, directory=""):
            nonlocal probe_count
            probe_count += 1
            if probe_count < 3:
                raise error("gateway_unavailable", "connection refused")
            return await original_providers(directory=directory)

        transport.providers = flaky_providers
        service = ExecutionService(
            transport,
            backoff_base=0,
            readiness_retries=2,
            readiness_backoff=0,
        )
        result = await service.execute(ExecutionRequest("hello", model="opencode/big-pickle", billing_mode="strict-model"))
        assert result.text == "ok"
        assert probe_count == 3
        assert len(result.attempts) == 1
        assert transport.created == ["s1"]

    asyncio.run(run())


def test_execution_forwards_agent_and_variant_to_transport():
    async def run():
        transport = FakeTransport()
        service = ExecutionService(transport, backoff_base=0)
        await service.execute(
            ExecutionRequest(
                "hello",
                model="opencode/big-pickle",
                billing_mode="strict-model",
                agent="plan-agent",
                variant="balanced",
            )
        )
        assert transport.chat_options == [{"agent": "plan-agent", "variant": "balanced"}]

    asyncio.run(run())


def test_quota_switches_model_and_continues_batch():
    async def run():
        transport = FakeTransport()
        transport.failures["opencode/big-pickle"] = lambda: error("quota_exhausted", "quota exhausted until 2099-01-01T00:00:00Z", 402)
        service = ExecutionService(transport, concurrency=1, backoff_base=0)
        results = [item async for item in service.execute_batch(ExecutionRequest("", task="fast-extraction"), ["one", "two"])]
        assert transport.provider_calls == 1
        assert all(item.outcome == "succeeded" for item in results)
        assert all(item.result.model != "opencode/big-pickle" for item in results)
    asyncio.run(run())


def test_explicit_session_handle_is_reused():
    async def run():
        transport = FakeTransport()
        service = ExecutionService(transport, backoff_base=0)
        result = await service.execute(ExecutionRequest("continue", model="opencode/big-pickle", billing_mode="strict-model", session_mode="continue", session=SessionHandle("existing")))
        assert transport.created == []
        assert transport.deleted == []
        assert result.session.session_id == "existing"
    asyncio.run(run())


def test_batch_concurrency_is_bounded():
    async def run():
        transport = FakeTransport(delay=0.01)
        service = ExecutionService(transport, concurrency=2, backoff_base=0)
        results = [item async for item in service.execute_batch(ExecutionRequest(""), [BatchItem(i, str(i)) for i in range(5)])]
        assert len(results) == 5
        assert transport.max_active <= 2
    asyncio.run(run())


def test_plan_exhaustion_marks_queued_items_not_started():
    async def run():
        transport = FakeTransport(fail_all=True)
        service = ExecutionService(transport, concurrency=1, backoff_base=0)
        results = [item async for item in service.execute_batch(ExecutionRequest(""), ["one", "two", "three"])]
        assert results[0].outcome == "failed"
        assert {item.outcome for item in results[1:]} == {"not_started"}
    asyncio.run(run())


def test_health_latency_is_ignored_until_third_sample():
    async def run():
        registry = HealthRegistry()
        await registry.record_success("slow", ttft=3, total_latency=3)
        await registry.record_success("slow", ttft=3, total_latency=3)
        assert await registry.order(["slow", "new"]) == ["slow", "new"]
        await registry.record_success("slow", ttft=3, total_latency=3)
        await registry.record_success("fast", ttft=1, total_latency=1)
        await registry.record_success("fast", ttft=1, total_latency=1)
        await registry.record_success("fast", ttft=1, total_latency=1)
        assert await registry.order(["slow", "fast"]) == ["fast", "slow"]
    asyncio.run(run())
