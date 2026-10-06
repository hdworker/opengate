from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, AsyncIterator, Iterable, Literal

from .catalog import (
    BILLING_MODES,
    BillingMode,
    CatalogParseError,
    CandidatePlan,
    CatalogSnapshot,
    NoCandidatePlanError,
    PREFERRED_MODELS,
    PRESETS,
    build_candidate_plan,
    explicit_model_satisfies_billing_mode,
    snapshot_catalog,
)
from .errors import ErrorDiagnostic, ExecutionError, classify_error, sanitize_diagnostic
from .structured import StructuredOutputError, StructuredSchemaError, json_instruction, parse_structured
from .transport import ExecutionTransport, OpenCodeSdkTransport
from ._values import dump_sdk_value


SessionMode = Literal["attempt", "continue"]
_SESSION_DELETE_TIMEOUT = 5.0


@dataclass(frozen=True)
class SessionHandle:
    session_id: str
    directory: str = ""
    routing_context: str = ""


@dataclass(frozen=True)
class ExecutionRequest:
    prompt: str
    system: str = ""
    task: str = ""
    model: str = ""
    billing_mode: BillingMode = "free-first"
    timeout: float = 180.0
    directory: str = ""
    session_mode: SessionMode = "attempt"
    session: SessionHandle | None = None
    session_title: str = "OpenGate execution"
    agent: str = ""
    variant: str = ""
    keep_session: bool = False
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class Attempt:
    number: int
    model: str
    provider: str
    session_id: str
    stage: str
    outcome: Literal["succeeded", "failed"]
    started_at: datetime
    finished_at: datetime
    ttft: float | None = None
    total_latency: float | None = None
    diagnostic: ErrorDiagnostic | None = None
    error_kind: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    text: str
    model: str
    session: SessionHandle | None
    attempts: tuple[Attempt, ...]
    ttft: float | None
    total_latency: float
    catalog: CatalogSnapshot
    structured: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchItem:
    index: int
    prompt: str
    key: str = ""


@dataclass(frozen=True)
class ItemResult:
    index: int
    key: str
    outcome: Literal["succeeded", "failed", "not_started"]
    result: ExecutionResult | None = None
    error: ExecutionError | None = None
    attempts: tuple[Attempt, ...] = ()


@dataclass
class _ModelHealth:
    samples: int = 0
    ewma_ttft: float | None = None
    ewma_total_latency: float | None = None
    transport_healthy: bool = True
    quota_until: datetime | None = None
    last_error: ErrorDiagnostic | None = None


class HealthRegistry:
    """Per-service routing memory; it intentionally has no persistence."""

    def __init__(self, *, ewma_alpha: float = 0.35) -> None:
        self.ewma_alpha = ewma_alpha
        self._models: dict[str, _ModelHealth] = {}
        self._lock = asyncio.Lock()

    async def is_blocked(self, model: str, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            health = self._models.get(model)
            if health is None or health.quota_until is None:
                return False
            if health.quota_until <= now:
                health.quota_until = None
                return False
            return True

    async def record_success(self, model: str, *, ttft: float | None, total_latency: float) -> None:
        async with self._lock:
            health = self._models.setdefault(model, _ModelHealth())
            health.samples += 1
            health.transport_healthy = True
            health.last_error = None
            health.ewma_ttft = _ewma(health.ewma_ttft, ttft, self.ewma_alpha)
            health.ewma_total_latency = _ewma(health.ewma_total_latency, total_latency, self.ewma_alpha)

    async def record_transport_reachable(self, model: str) -> None:
        async with self._lock:
            self._models.setdefault(model, _ModelHealth()).transport_healthy = True

    async def record_error(
        self,
        model: str,
        diagnostic: ErrorDiagnostic,
        *,
        quota_until: datetime | None = None,
        transport_failure: bool = False,
    ) -> None:
        async with self._lock:
            health = self._models.setdefault(model, _ModelHealth())
            health.last_error = diagnostic
            status = diagnostic.status
            if status is not None:
                health.transport_healthy = 100 <= status < 500
            elif transport_failure:
                health.transport_healthy = False
            if quota_until is not None:
                health.quota_until = quota_until

    async def order(
        self,
        models: Iterable[str],
        *,
        priorities: dict[str, int] | None = None,
        free_models: Iterable[str] = (),
    ) -> list[str]:
        """Order by billing tier, policy priority, health, then mature latency."""
        priorities = priorities or {}
        free = set(free_models)
        async with self._lock:
            source = list(models)
            indexed = {key: index for index, key in enumerate(source)}

            def sort_key(key: str) -> tuple[int, int, int, float, float, int]:
                health = self._models.get(key)
                mature = health is not None and health.samples >= 3
                return (
                    0 if key in free else 1,
                    priorities.get(key, 99),
                    0 if health is None or health.transport_healthy else 1,
                    health.ewma_total_latency if mature and health and health.ewma_total_latency is not None else float("inf"),
                    health.ewma_ttft if mature and health and health.ewma_ttft is not None else float("inf"),
                    indexed[key],
                )

            return sorted(source, key=sort_key)

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            return {
                key: {
                    "samples": value.samples,
                    "ewma_ttft": value.ewma_ttft,
                    "ewma_total_latency": value.ewma_total_latency,
                    "transport_healthy": value.transport_healthy,
                    "quota_until": value.quota_until.isoformat() if value.quota_until else None,
                    "last_error": value.last_error,
                }
                for key, value in self._models.items()
            }


@dataclass
class _RunState:
    snapshot: CatalogSnapshot
    plan: CandidatePlan
    structured_instruction: str = ""
    response_schema: dict[str, Any] | None = None
    local_blocked: set[str] = field(default_factory=set)
    exhausted: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def is_exhausted(self) -> bool:
        async with self.lock:
            return self.exhausted

    async def block_local(self, model: str) -> None:
        async with self.lock:
            self.local_blocked.add(model)

    async def is_locally_blocked(self, model: str) -> bool:
        async with self.lock:
            return model in self.local_blocked

    async def mark_exhausted(self) -> None:
        async with self.lock:
            self.exhausted = True


def _ewma(previous: float | None, value: float | None, alpha: float) -> float | None:
    if value is None:
        return previous
    return value if previous is None else alpha * value + (1 - alpha) * previous


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _split_model(key: str) -> tuple[str, str]:
    provider, separator, model = key.partition("/")
    return (provider, model) if separator else ("opencode", key)


def _assistant_text(messages: Any, *, sensitive_values: tuple[str, ...] = ()) -> str:
    raw_messages = dump_sdk_value(messages)
    if isinstance(raw_messages, dict):
        raw_messages = raw_messages.get("messages") or raw_messages.get("data") or []
    if not isinstance(raw_messages, (list, tuple)):
        raw_messages = list(raw_messages or [])

    latest_user_index = -1
    for index, message in enumerate(raw_messages):
        info = dump_sdk_value(_value(message, "info", {})) or {}
        if _value(info, "role", "") == "user":
            latest_user_index = index

    for index in range(len(raw_messages) - 1, latest_user_index, -1):
        message = raw_messages[index]
        info = dump_sdk_value(_value(message, "info", {})) or {}
        if _value(info, "role", "") != "assistant":
            continue
        parts = dump_sdk_value(_value(message, "parts", [])) or []
        text_parts: list[str] = []
        for part in parts:
            part_data = dump_sdk_value(part)
            if _value(part_data, "type", "") == "text":
                text_parts.append(str(_value(part_data, "text", "")))
        text = "".join(text_parts)
        if text:
            return text
        error = dump_sdk_value(_value(info, "error"))
        if error:
            error_data = sanitize_diagnostic(
                error if isinstance(error, dict) else {"error": str(error)},
                sensitive_values=sensitive_values,
            )
            message_text = str(error_data.get("message") or error_data.get("name") or error)
            message_text = str(sanitize_diagnostic(message_text, sensitive_values=sensitive_values))
            diagnostic = ErrorDiagnostic(
                stage="answer_poll",
                message=message_text,
                source_name=str(error_data.get("name") or "OpenCodeAssistantError"),
                source_data=error_data,
            )
            raise ExecutionError(message_text, kind=classify_error(None, message_text, source_name=diagnostic.source_name), diagnostic=diagnostic)
    raise ExecutionError(
        "OpenCode returned no assistant text",
        kind="invalid_response",
        diagnostic=ErrorDiagnostic(stage="answer_poll", message="OpenCode returned no assistant text", source_name="OpenCodeResponse"),
    )


def _quota_expiry(message: str, *, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    iso = re.search(r"\b20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b", message)
    if iso:
        value = iso.group(0).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except ValueError:
            pass
    duration = re.search(r"(?:in|after)\s+(\d+)\s*(seconds?|minutes?|hours?|days?)", message.casefold())
    if duration:
        amount = int(duration.group(1))
        unit = duration.group(2)
        key = "seconds" if unit.startswith("second") else "minutes" if unit.startswith("minute") else "hours" if unit.startswith("hour") else "days"
        return now + timedelta(**{key: amount})
    return None


def _not_started(item: BatchItem) -> ItemResult:
    error = ExecutionError(
        "Item was not scheduled because the Run candidate plan was exhausted",
        kind="plan_exhausted",
        diagnostic=ErrorDiagnostic(stage="batch_schedule", message="candidate plan exhausted"),
    )
    return ItemResult(item.index, item.key, "not_started", error=error)


class ExecutionService:
    def __init__(
        self,
        transport: ExecutionTransport,
        *,
        concurrency: int = 2,
        backoff_base: float = 0.25,
        readiness_retries: int = 3,
        readiness_backoff: float = 1.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if readiness_retries < 0:
            raise ValueError("readiness_retries must not be negative")
        if readiness_backoff < 0:
            raise ValueError("readiness_backoff must not be negative")
        self.transport = transport
        self.concurrency = concurrency
        self.backoff_base = backoff_base
        self.readiness_retries = readiness_retries
        self.readiness_backoff = readiness_backoff
        self.health = HealthRegistry()

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        concurrency: int = 2,
        timeout: float | None = None,
        readiness_retries: int | None = None,
        readiness_backoff: float | None = None,
    ) -> "ExecutionService":
        import os

        return cls.from_config(
            base_url=base_url or os.getenv("OPENGATE_URL", "http://127.0.0.1:4096"),
            timeout=float(timeout if timeout is not None else os.getenv("OPENGATE_TIMEOUT_SECONDS", os.getenv("OPENGATE_TIMEOUT", "180"))),
            username=os.getenv("OPENGATE_USERNAME", os.getenv("OPENCODE_USERNAME", "")),
            password=os.getenv("OPENGATE_PASSWORD", os.getenv("OPENCODE_PASSWORD", "")),
            concurrency=concurrency,
            readiness_retries=int(readiness_retries if readiness_retries is not None else os.getenv("OPENGATE_READINESS_RETRIES", "3")),
            readiness_backoff=float(readiness_backoff if readiness_backoff is not None else os.getenv("OPENGATE_READINESS_BACKOFF_SECONDS", "1")),
        )

    @classmethod
    def from_config(
        cls,
        *,
        base_url: str = "http://127.0.0.1:4096",
        timeout: float = 180.0,
        username: str = "",
        password: str = "",
        concurrency: int = 2,
        readiness_retries: int = 3,
        readiness_backoff: float = 1.0,
    ) -> "ExecutionService":
        """Build the public execution module without exposing transport details."""

        return cls(
            OpenCodeSdkTransport(
                base_url,
                timeout=timeout,
                username=username,
                password=password,
            ),
            concurrency=concurrency,
            readiness_retries=readiness_retries,
            readiness_backoff=readiness_backoff,
        )

    async def catalog_snapshot(self, *, directory: str = "") -> CatalogSnapshot:
        payload = await self.transport.providers(directory=directory)
        try:
            return snapshot_catalog(payload)
        except CatalogParseError as exc:
            message = str(sanitize_diagnostic(str(exc))) or "OpenCode returned an invalid model catalog"
            raise ExecutionError(
                message,
                kind="invalid_response",
                diagnostic=ErrorDiagnostic(
                    stage="catalog/parse",
                    message=message,
                    source_name=exc.__class__.__name__,
                ),
            ) from exc

    async def health_snapshot(self) -> dict[str, Any]:
        return await self.health.snapshot()

    async def backend_health(self) -> dict[str, Any]:
        """Probe the configured OpenCode backend through the transport seam."""

        return await self.transport.health()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.session_mode == "continue" and request.session is None:
            raise ExecutionError("continue session mode requires SessionHandle", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="SessionHandle is required"))
        state = await self._prepare_run(request)
        item = BatchItem(0, request.prompt)
        result = await self._execute_item(request, item, state)
        if result.outcome == "succeeded" and result.result is not None:
            return result.result
        raise result.error or ExecutionError("OpenGate execution failed", attempts=list(result.attempts))

    def execute_batch(self, request: ExecutionRequest, items: Iterable[BatchItem | str | dict[str, Any]]) -> AsyncIterator[ItemResult]:
        return self._execute_batch(request, items)

    async def _execute_batch(self, request: ExecutionRequest, items: Iterable[BatchItem | str | dict[str, Any]]) -> AsyncIterator[ItemResult]:
        state = await self._prepare_run(request)
        iterator = iter(items)
        pending: set[asyncio.Task[ItemResult]] = set()
        next_index = 0
        exhausted = False

        async def worker(item: BatchItem) -> ItemResult:
            if await state.is_exhausted():
                return _not_started(item)
            return await self._execute_item(request, item, state)

        try:
            while pending or not exhausted:
                while not exhausted and len(pending) < self.concurrency:
                    try:
                        raw_item = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    item = self._coerce_item(raw_item, next_index)
                    next_index += 1
                    pending.add(asyncio.create_task(worker(item)))

                if not pending:
                    break

                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    yield task.result()
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            close_iterator = getattr(iterator, "close", None)
            if callable(close_iterator):
                try:
                    close_iterator()
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Failed to close batch input iterator: %s",
                        sanitize_diagnostic(str(exc)),
                    )

    async def _prepare_run(self, request: ExecutionRequest) -> _RunState:
        if request.billing_mode not in BILLING_MODES:
            raise ExecutionError(f"Unknown billing mode: {request.billing_mode}", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="billing_mode is invalid"))
        if not isinstance(request.task, str) or not isinstance(request.model, str):
            raise ExecutionError("task and model must be strings", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="task and model must be strings"))
        if request.billing_mode == "strict-model" and not request.model:
            raise ExecutionError("strict-model requires an explicit model", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="model is required"))
        if request.timeout <= 0:
            raise ExecutionError("timeout must be positive", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="timeout must be positive"))
        if request.task and not request.model and request.task not in PRESETS:
            message = str(sanitize_diagnostic(f"Unknown OpenGate task preset: {request.task}"))
            raise ExecutionError(
                message,
                kind="invalid_input",
                diagnostic=ErrorDiagnostic(
                    stage="input/task",
                    message=message,
                    source_name="UnknownTaskPreset",
                    source_data={"task": request.task, "billing_mode": request.billing_mode},
                ),
            )
        structured_instruction = ""
        response_schema = None
        if request.response_schema is not None:
            try:
                response_schema = copy.deepcopy(request.response_schema)
                structured_instruction = json_instruction(response_schema)
            except Exception as exc:
                sensitive_values = tuple(value for value in (request.prompt, request.system) if isinstance(value, str))
                message = str(sanitize_diagnostic(str(exc), sensitive_values=sensitive_values))
                raise ExecutionError(
                    message or "Invalid structured-output schema",
                    kind="invalid_input",
                    diagnostic=ErrorDiagnostic(stage="input/structured_schema", message=message or "Invalid structured-output schema", source_name=exc.__class__.__name__),
                ) from exc
        snapshot = await self._ready_catalog_snapshot(directory=request.directory)
        if request.model and not explicit_model_satisfies_billing_mode(snapshot, request.model, request.billing_mode):
            raise ExecutionError(f"Model {request.model} does not satisfy {request.billing_mode} billing mode", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="model is incompatible with billing_mode", model=request.model))
        try:
            plan = build_candidate_plan(snapshot, task=request.task, model=request.model, billing_mode=request.billing_mode)
        except ExecutionError:
            raise
        except NoCandidatePlanError as exc:
            message = str(sanitize_diagnostic(str(exc))) or "No model candidates available"
            raise ExecutionError(
                message,
                kind="model_unavailable",
                diagnostic=ErrorDiagnostic(
                    stage="routing/planning",
                    message=message,
                    source_name=exc.__class__.__name__,
                    source_data={
                        "task": request.task,
                        "billing_mode": request.billing_mode,
                        "catalog_model_count": len(snapshot.models),
                    },
                    model=request.model,
                ),
            ) from exc
        except ValueError as exc:
            # Unknown tasks are rejected explicitly above; remaining planner
            # ValueErrors indicate malformed request/catalog data.
            message = str(sanitize_diagnostic(str(exc))) or "Candidate planning failed"
            raise ExecutionError(
                message,
                kind="invalid_response",
                diagnostic=ErrorDiagnostic(
                    stage="catalog/planning",
                    message=message,
                    source_name=exc.__class__.__name__,
                    source_data={
                        "task": request.task,
                        "billing_mode": request.billing_mode,
                        "catalog_model_count": len(snapshot.models),
                    },
                ),
            ) from exc
        except Exception as exc:
            message = str(sanitize_diagnostic(str(exc))) or "Candidate planning failed"
            raise ExecutionError(
                message,
                kind="invalid_response",
                diagnostic=ErrorDiagnostic(
                    stage="catalog/planning",
                    message=message,
                    source_name=exc.__class__.__name__,
                    source_data={
                        "task": request.task,
                        "billing_mode": request.billing_mode,
                        "catalog_model_count": len(snapshot.models),
                    },
                ),
            ) from exc
        return _RunState(snapshot, plan, structured_instruction, response_schema)

    async def _ready_catalog_snapshot(self, *, directory: str = "") -> CatalogSnapshot:
        """Wait briefly for a starting gateway without retrying a model request."""

        last_error: ExecutionError | None = None
        for probe_number in range(self.readiness_retries + 1):
            try:
                return await self.catalog_snapshot(directory=directory)
            except ExecutionError as exc:
                last_error = exc
                if exc.kind != "gateway_unavailable" or probe_number >= self.readiness_retries:
                    raise
                await asyncio.sleep(self.readiness_backoff * (probe_number + 1))
        raise last_error or ExecutionError(
            "OpenGate readiness probe failed",
            kind="gateway_unavailable",
            diagnostic=ErrorDiagnostic(stage="catalog", message="readiness probe failed"),
        )

    @staticmethod
    def _coerce_item(value: BatchItem | str | dict[str, Any], index: int) -> BatchItem:
        if isinstance(value, BatchItem):
            return value
        if isinstance(value, str):
            return BatchItem(index, value)
        if isinstance(value, dict):
            return BatchItem(int(value.get("index", index)), str(value.get("prompt", "")), str(value.get("key", "")))
        raise TypeError("batch items must be BatchItem, str, or mapping")

    async def _execute_item(self, request: ExecutionRequest, item: BatchItem, state: _RunState) -> ItemResult:
        if not isinstance(item.prompt, str) or not item.prompt.strip():
            error = ExecutionError("prompt must not be empty", kind="invalid_input", diagnostic=ErrorDiagnostic(stage="input", message="prompt must not be empty"))
            return ItemResult(item.index, item.key, "failed", error=error)
        model_prompt = item.prompt
        if state.response_schema is not None:
            model_prompt += "\n\n" + state.structured_instruction
        priorities = {key: index for index, key in enumerate(PREFERRED_MODELS.get(request.task, ())) }
        candidates = await self.health.order(
            state.plan.ordered,
            priorities=priorities,
            free_models=state.plan.free,
        )
        attempts: list[Attempt] = []
        only_exhausting_errors = True
        last_error: ExecutionError | None = None
        attempt_number = 0
        for candidate_index, candidate in enumerate(candidates):
            if await state.is_locally_blocked(candidate) or await self.health.is_blocked(candidate):
                continue
            provider, model = _split_model(candidate)
            gateway_retries = 0
            while True:
                attempt_number += 1
                started_at = datetime.now(timezone.utc)
                started_clock = monotonic()
                session_id = ""
                keep_session = request.keep_session or request.session_mode == "continue" or request.session is not None
                session_state = {
                    "session_id": request.session.session_id
                    if request.session is not None and request.session_mode == "continue" and not attempts
                    else ""
                }
                transport_invoke_failed = False
                try:
                    async def invoke() -> Any:
                        if not session_state["session_id"]:
                            created = await self.transport.create_session(title=request.session_title, directory=request.directory)
                            session_state["session_id"] = str(_value(dump_sdk_value(created), "id", ""))
                        if not session_state["session_id"]:
                            raise ExecutionError("OpenCode returned no session id", kind="invalid_response", diagnostic=ErrorDiagnostic(stage="session_create", message="missing session id", model=candidate, provider=provider))
                        await self.transport.chat(
                            session_state["session_id"],
                            prompt=model_prompt,
                            provider=provider,
                            model=model,
                            system=request.system,
                            directory=request.directory,
                            agent=request.agent,
                            variant=request.variant,
                        )
                        return await self.transport.messages(session_state["session_id"], directory=request.directory)

                    try:
                        messages = await asyncio.wait_for(invoke(), timeout=request.timeout)
                    except asyncio.TimeoutError as exc:
                        transport_invoke_failed = True
                        raise ExecutionError(
                            f"OpenGate attempt timed out after {request.timeout:g}s",
                            kind="gateway_unavailable",
                            diagnostic=ErrorDiagnostic(stage="answer_poll", message=f"attempt timed out after {request.timeout:g}s", model=candidate, provider=provider),
                            session_id=session_state["session_id"],
                        ) from exc
                    except ExecutionError:
                        transport_invoke_failed = True
                        raise
                    except Exception as exc:
                        transport_invoke_failed = True
                        message = str(sanitize_diagnostic(str(exc), sensitive_values=(item.prompt, model_prompt, request.system)))
                        diagnostic = ErrorDiagnostic(stage="execution/internal", message=message[:2_000] or "Unexpected transport adapter failure", source_name=exc.__class__.__name__, model=candidate, provider=provider)
                        raise ExecutionError(
                            diagnostic.message,
                            kind="internal_error",
                            diagnostic=diagnostic,
                            session_id=session_state["session_id"],
                        ) from exc

                    await self.health.record_transport_reachable(candidate)
                    session_id = session_state["session_id"]
                    try:
                        text = _assistant_text(messages, sensitive_values=(item.prompt, model_prompt, request.system))
                        structured = None
                        if state.response_schema is not None:
                            try:
                                structured = parse_structured(text, state.response_schema)
                            except StructuredSchemaError as exc:
                                raise ExecutionError(
                                    str(exc),
                                    kind="invalid_input",
                                    diagnostic=ErrorDiagnostic(stage="input/structured_schema", message=str(exc), source_name="OpenGateStructuredSchema", model=candidate, provider=provider),
                                    session_id=session_id,
                                ) from exc
                            except StructuredOutputError as exc:
                                raise ExecutionError(
                                    str(exc),
                                    kind="invalid_response",
                                    diagnostic=ErrorDiagnostic(
                                        stage="structured_output",
                                        message=str(exc),
                                        source_name="OpenGateStructuredOutput",
                                        source_data={"path": exc.path, "schema": state.response_schema},
                                        model=candidate,
                                        provider=provider,
                                    ),
                                    session_id=session_id,
                                ) from exc
                            except (TypeError, ValueError, RecursionError) as exc:
                                message = str(sanitize_diagnostic(str(exc), sensitive_values=(item.prompt, model_prompt, request.system)))
                                raise ExecutionError(
                                    message or "Invalid structured-output schema",
                                    kind="invalid_input",
                                    diagnostic=ErrorDiagnostic(stage="input/structured_schema", message=message or "Invalid structured-output schema", source_name=exc.__class__.__name__, model=candidate, provider=provider),
                                    session_id=session_id,
                                ) from exc
                        total_latency = monotonic() - started_clock
                        ttft = None
                        finished_at = datetime.now(timezone.utc)
                        attempt = Attempt(attempt_number, candidate, provider, session_id, "answer_poll", "succeeded", started_at, finished_at, ttft, total_latency)
                        handle = SessionHandle(session_id, request.directory, candidate) if keep_session else None
                        metadata = {"session_id": session_id, "session_mode": request.session_mode, "session_title": request.session_title}
                        result = ExecutionResult(text, candidate, handle, tuple(attempts + [attempt]), ttft, total_latency, state.snapshot, structured, metadata)
                        await self.health.record_success(candidate, ttft=ttft, total_latency=total_latency)
                        attempts.append(attempt)
                        return ItemResult(item.index, item.key, "succeeded", result, attempts=tuple(attempts))
                    except ExecutionError:
                        raise
                    except Exception as exc:
                        message = str(sanitize_diagnostic(str(exc), sensitive_values=(item.prompt, model_prompt, request.system)))
                        diagnostic = ErrorDiagnostic(
                            stage="execution/internal",
                            message=message[:2_000] or "Unexpected local execution error",
                            source_name=exc.__class__.__name__,
                            model=candidate,
                            provider=provider,
                        )
                        error = ExecutionError(diagnostic.message, kind="internal_error", diagnostic=diagnostic, session_id=session_id)
                        attempts.append(Attempt(attempt_number, candidate, provider, session_id, diagnostic.stage, "failed", started_at, datetime.now(timezone.utc), None, monotonic() - started_clock, diagnostic, error.kind))
                        error.attempts = list(attempts)
                        return ItemResult(item.index, item.key, "failed", error=error, attempts=tuple(attempts))
                except ExecutionError as exc:
                    session_id = exc.session_id or session_state["session_id"] or session_id
                    diagnostic = exc.diagnostic or ErrorDiagnostic(stage="execution", message=str(exc), model=candidate, provider=provider)
                    if not diagnostic.model or not diagnostic.provider:
                        diagnostic = ErrorDiagnostic(diagnostic.stage, diagnostic.message, diagnostic.status, diagnostic.source_name, diagnostic.source_data, candidate, provider)
                    exc.diagnostic = diagnostic
                    exc.session_id = session_id
                    exc.attempts = list(attempts)
                    total_latency = monotonic() - started_clock
                    attempts.append(Attempt(attempt_number, candidate, provider, session_id, diagnostic.stage, "failed", started_at, datetime.now(timezone.utc), None, total_latency, diagnostic, exc.kind))
                    exc.attempts = list(attempts)
                    last_error = exc
                    await self.health.record_error(
                        candidate,
                        diagnostic,
                        quota_until=_quota_expiry(diagnostic.message) if exc.kind == "quota_exhausted" else None,
                        transport_failure=transport_invoke_failed and exc.kind == "gateway_unavailable",
                    )
                    if exc.kind == "quota_exhausted":
                        if _quota_expiry(diagnostic.message) is None:
                            await state.block_local(candidate)
                    elif exc.kind == "model_unavailable":
                        await state.block_local(candidate)
                    else:
                        only_exhausting_errors = False
                    if exc.kind == "gateway_unavailable" and transport_invoke_failed and gateway_retries < 2:
                        gateway_retries += 1
                        await asyncio.sleep(min(max(self.backoff_base * gateway_retries, 0.0), request.timeout, 5.0))
                        continue
                    if exc.kind == "rate_limited":
                        has_next_candidate = False
                        for next_candidate in candidates[candidate_index + 1:]:
                            if not await state.is_locally_blocked(next_candidate) and not await self.health.is_blocked(next_candidate):
                                has_next_candidate = True
                                break
                        if has_next_candidate:
                            await asyncio.sleep(min(max(self.backoff_base, 0.0), request.timeout, 5.0))
                    if exc.kind in {"invalid_input", "request_rejected", "invalid_response", "internal_error"}:
                        return ItemResult(item.index, item.key, "failed", error=exc, attempts=tuple(attempts))
                    break
                finally:
                    cleanup_session_id = session_state["session_id"] or session_id
                    if cleanup_session_id and not keep_session:
                        try:
                            await asyncio.wait_for(
                                self.transport.delete_session(cleanup_session_id, directory=request.directory),
                                timeout=_SESSION_DELETE_TIMEOUT,
                            )
                        except Exception as cleanup_exc:
                            logging.getLogger(__name__).warning(
                                "OpenCode session cleanup failed for %s: %s",
                                cleanup_session_id,
                                sanitize_diagnostic(str(cleanup_exc), sensitive_values=(item.prompt, model_prompt, request.system)),
                            )
        if only_exhausting_errors and last_error is not None:
            await state.mark_exhausted()
        if last_error is None:
            last_error = ExecutionError("No candidate model is available for this Run", kind="plan_exhausted", diagnostic=ErrorDiagnostic(stage="routing", message="candidate plan exhausted"))
            await state.mark_exhausted()
        return ItemResult(item.index, item.key, "failed", error=last_error, attempts=tuple(attempts))

    async def close(self) -> None:
        await self.transport.close()

    async def __aenter__(self) -> "ExecutionService":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
