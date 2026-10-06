# OpenGate CLI

[Русский](#русский) | [English](#english)

## Русский

OpenGate — переиспользуемый Python-компонент для доступа проектов к моделям
через уже запущенный OpenCode Serve.

Компонент сам выбирает и ротирует модели по заданной политике, учитывая:

- live-каталог OpenCode;
- совместимость модели с preset;
- доступность и quota TTL;
- наблюдаемую скорость успешных вызовов.

Основной интерфейс — асинхронный Python API. MCP-слой остаётся тонким
интеграционным адаптером проекта.

~~~text
MCP agent -> project adapter -> project worker -> OpenGate ExecutionService
                                                        -> OpenCode Serve
~~~

## Установка в другой проект

### Локальное подключение из checkout

В виртуальном окружении проекта установите пакет в editable-режиме:

~~~powershell
uv venv
uv pip install -e "C:\Code\opengate"
~~~

Если проект использует MCP runtime, добавьте extra:

~~~powershell
uv pip install -e "C:\Code\opengate[mcp]"
~~~

Для разработки самого OpenGate:

~~~powershell
uv pip install -e "C:\Code\opengate[mcp,dev]"
~~~

Core-зависимость opencode-ai устанавливается автоматически. MCP не является
обязательной зависимостью execution API.

## Требование к OpenCode Serve

OpenGate подключается к уже работающему локальному OpenCode Serve и не
запускает его из Python API. Gateway должен быть доступен на loopback-адресе
или на Docker host gateway, например http://127.0.0.1:4096 или
http://host.docker.internal:4096.

Проверить или запустить gateway из checkout OpenGate:

~~~powershell
opengate ensure
opengate models
~~~

В production запуск процесса OpenCode Serve остаётся ответственностью
bootstrap/deployment проекта.

## Конфигурация

ExecutionService.from_env() читает:

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| OPENGATE_URL | http://127.0.0.1:4096 | URL OpenCode Serve |
| OPENGATE_TIMEOUT_SECONDS | 180 | timeout SDK-транспорта в секундах |
| OPENGATE_USERNAME | пусто | Basic Auth username |
| OPENGATE_PASSWORD | пусто | Basic Auth password |

`OPENGATE_TIMEOUT`, `OPENCODE_USERNAME` и `OPENCODE_PASSWORD` остаются
совместимыми legacy-алиасами.

`ExecutionService.from_env(base_url=...)` позволяет явно переопределить URL
из `OPENGATE_URL`; CLI-флаг `--url` использует тот же путь конфигурации.

Credentials нельзя сохранять в Git, prompt, диагностике или результатах batch.

## Минимальный Python API

~~~python
import asyncio

from opengate import ExecutionRequest, ExecutionService


async def main() -> None:
    async with ExecutionService.from_env() as service:
        result = await service.execute(
            ExecutionRequest(
                prompt="Верни JSON с тремя ключевыми фактами о тексте.",
                task="fast-extraction",
            )
        )

    print(result.text)
    print("model:", result.model)
    print("attempts:", len(result.attempts))
    print("latency:", result.total_latency)


asyncio.run(main())
~~~

ExecutionResult содержит text, итоговую model, ordered attempts, ttft,
total_latency, использованный catalog snapshot, optional session, metadata и
parsed structured value, если consumer передал response_schema.

### Последовательные смысловые итерации и строгий JSON

Проект владеет контекстом предыдущей итерации, а OpenGate предоставляет
транспортную сессию и проверку результата:

~~~python
from opengate import ExecutionRequest, ExecutionService, SessionHandle

schema = {
    "type": "object",
    "required": ["assignments"],
    "properties": {"assignments": {"type": "array"}},
}

async with ExecutionService.from_env() as service:
    first = await service.execute(ExecutionRequest(
        prompt="Разметь первый batch.", response_schema=schema, keep_session=True,
    ))
    second = await service.execute(ExecutionRequest(
        prompt="Продолжи по следующему batch и сохрани смысл предыдущей итерации.",
        response_schema=schema, session_mode="continue", session=first.session,
        keep_session=True,
    ))
~~~

При нарушении JSON-контракта возникает `ExecutionError(kind="invalid_response")`.
В `diagnostic.source_data` сохраняются путь ошибки и схема, но не credentials.
OpenGate не интерпретирует поля схемы и не знает доменную семантику групп.

Поддерживается явное подмножество JSON Schema: `type`, `enum`, `required`,
`properties`, `items` и `additionalProperties`, включая булевы subschemas
`true` и `false`. Неизвестные ключевые слова отклоняются до обращения к
transport. Невалидный JSON, включая `NaN` и `Infinity`, возвращает
`invalid_response`. Сравнение значений в `enum` учитывает JSON-типы: `true` не
равно `1`, а числа `1` и `1.0` считаются эквивалентными.

## Политика моделей

По умолчанию используется free-first: сначала free candidates, затем
совместимые paid candidates.

| billing_mode | Поведение |
| --- | --- |
| free-first | free candidates, затем paid candidates |
| free-only | только free candidates |
| paid-only | только paid candidates |
| strict-model | только явно указанный model |

~~~python
request = ExecutionRequest(
    prompt="Сделай глубокий анализ.",
    model="opencode-go/qwen3.7-max",
    billing_mode="strict-model",
)
~~~

Если MCP agent интерпретировал запрос как требующий paid-модели, он передаёт
разрешённую политику в ExecutionRequest. Сам компонент не извлекает billing
intent из произвольного текста prompt.

Известные free identities:

~~~text
opencode/big-pickle
opencode/mimo-v2.5-free
opencode/ling-3.0-flash-fin-free
opencode/nemotron-3-ultra-free
opencode/nemotron-3.5-lightning-free
opencode/muse-spark-1.3-contributor-free
~~~

Каталог является advisory-источником. Если известная free identity отсутствует
в snapshot, она всё равно может быть проверена реальным вызовом.

## Presets и routing

Доступны presets:

~~~text
fast-extraction
bulk-classification
long-document-classification
deep-analysis
synthesis
million-synthesis
multimodal
~~~

Preset задаёт требования к context, reasoning, attachments и порядок внутри
своей группы приоритета. Сначала всегда идут free candidates, затем paid;
здоровье транспорта и измеренная скорость не могут поменять этот порядок.
Внутри одной группы приоритета кандидаты с доступным транспортом идут раньше
нездоровых, а runtime latency используется только после трёх успешных вызовов.

## Async batch

execute_batch() принимает строки, mapping или BatchItem и возвращает
результаты через async iterator:

~~~python
from opengate import ExecutionRequest, ExecutionService


async def process(items: list[str]) -> None:
    async with ExecutionService.from_env() as service:
        request = ExecutionRequest("", task="bulk-classification")

        async for item in service.execute_batch(request, items):
            if item.outcome == "succeeded":
                save_result(item.index, item.result.text)
            elif item.outcome == "not_started":
                record_not_started(item.index)
            else:
                record_error(item.index, item.error)
~~~

Batch использует default concurrency 2, запрашивает catalog один раз на Run и
возвращает результаты по мере завершения. При исчерпании полного free+paid
плана новые Items получают `outcome="not_started"` и `kind="plan_exhausted"`;
активные Attempts завершаются по своему timeout. Это состояние не означает,
что исчерпана quota.

Если consumer закрывает или отменяет async iterator, OpenGate отменяет
незавершённые локальные worker-задачи, включая выполняющиеся ими вызовы.
Это отличается от исчерпания плана: план прекращает только запуск новых
Items, а уже активные Attempts продолжаются.

Batch v1 не читает старый output как скрытый checkpoint и не поддерживает
автоматический cross-run resume. Consumer сам сохраняет ItemResult и решает,
какие Items явно повторно отправить в следующем Run.

## Sessions

По умолчанию каждый Attempt получает новую OpenCode session. Для явного
продолжения передайте SessionHandle:

~~~python
from opengate import ExecutionRequest, SessionHandle


request = ExecutionRequest(
    prompt="Продолжи предыдущий анализ.",
    model="opencode/big-pickle",
    billing_mode="strict-model",
    session_mode="continue",
    session=SessionHandle("session-id-from-previous-result"),
)
~~~

Session ID — provider metadata, а не project task identity. Durable task state,
идемпотентность и сохранение бизнес-результата принадлежат проекту.

## Ошибки

Ошибки поднимаются как ExecutionError. Основные kind:

~~~text
quota_exhausted
rate_limited
model_unavailable
gateway_unavailable
plan_exhausted
internal_error
request_rejected
invalid_response
invalid_input
~~~

~~~python
from opengate import ExecutionError


try:
    result = await service.execute(request)
except ExecutionError as error:
    print(error.kind)
    print(error.diagnostic)
    print("attempts:", len(error.attempts))
~~~

quota_exhausted переключает candidate и сохраняет quota TTL в памяти service.
model_unavailable сразу переключает candidate. Rate limit получает bounded
backoff. `ExecutionTransport` adapters должны преобразовывать внешние и
backend-ошибки в типизированный `ExecutionError`. Только
`ExecutionError(kind="gateway_unavailable")`, полученный от adapter во время
вызова, и timeout самого ExecutionService повторяются до двух раз на той же
модели. Неожиданное raw exception от adapter становится
`ExecutionError(kind="internal_error")` без повтора и переключения модели.
`plan_exhausted` означает, что Run исчерпал доступных кандидатов, а не что
провайдер сообщил об исчерпании quota. Rejected request, invalid response и
invalid input автоматически не повторяются.

Full prompt и credentials не входят в diagnostics. SDK retry отключён, поэтому
каждый retry и model switch виден в Attempt.

## CLI

~~~powershell
opengate execute "Return exactly OK" --task fast-extraction
opengate execute "Use paid model" --model opencode-go/qwen3.7-max --billing-mode strict-model
opengate batch input.jsonl output.jsonl --field text --task bulk-classification
~~~

CLI batch записывает результаты в output, но этот файл не становится checkpoint и
не используется для неявного пропуска строк при следующем запуске. CLI
отклоняет запуск, если input и output указывают на один файл, чтобы не
усечь входные данные.

## MCP и граница проекта

MCP scaffold нужен, когда агент обращается к доменным инструментам проекта:

~~~python
from opengate.mcp_runtime import LoopbackAPI, create_mcp_server


class ProjectAdapter:
    name = "My Project"
    instructions = "Use project tools through the local API."

    def register_tools(self, mcp, api: LoopbackAPI) -> None:
        @mcp.tool()
        async def project_get_context() -> dict:
            return await api.acall("GET", "/api/internal/mcp/context")


if __name__ == "__main__":
    create_mcp_server(ProjectAdapter()).run("stdio")
~~~

Для удалённого read-only MCP проект может явно включить HTTPS и bearer token:

~~~python
create_mcp_server(
    ProjectAdapter(),
    api_url="https://efir.example/internal",
    allow_remote=True,
    bearer_token="...",
)
~~~

`allow_remote=True` принимает только HTTPS; по умолчанию сохраняется loopback-only
режим. Ответственность разделена так:

- проект — prompts, source data, domain operations, validation и durable task;
- ExecutionService — catalog, candidate plan, sessions, retries и diagnostics;
- MCP adapter — безопасная loopback-интеграция агента с проектом.

Подробный контракт: [docs/protocol.md](docs/protocol.md).
Карта миграции: [docs/migration.md](docs/migration.md).

## English

OpenGate is a reusable Python module that lets projects access models through
an already running OpenCode Serve instance.

The module selects and rotates models according to an explicit policy, using:

- the live OpenCode catalog;
- model compatibility with the preset;
- availability and quota TTL;
- observed speed of successful calls.

The primary interface is the asynchronous Python API. The MCP layer remains a
thin project integration adapter.

~~~text
MCP agent -> project adapter -> project worker -> OpenGate ExecutionService
                                                        -> OpenCode Serve
~~~

## Installing in another project

### Connect locally from a checkout

Install the package in editable mode in the project's virtual environment:

~~~powershell
uv venv
uv pip install -e "C:\Code\opengate"
~~~

If the project uses the MCP runtime, install the extra:

~~~powershell
uv pip install -e "C:\Code\opengate[mcp]"
~~~

For OpenGate development:

~~~powershell
uv pip install -e "C:\Code\opengate[mcp,dev]"
~~~

The core `opencode-ai` dependency is installed automatically. MCP is not a
required dependency of the execution API.

## OpenCode Serve requirement

OpenGate connects to an already running local OpenCode Serve instance; the
Python API does not start it. The gateway must be reachable through a loopback
address or the Docker host gateway, for example
`http://127.0.0.1:4096` or `http://host.docker.internal:4096`.

Check or start the gateway from the OpenGate checkout:

~~~powershell
opengate ensure
opengate models
~~~

In production, starting the OpenCode Serve process remains the responsibility
of the project's bootstrap/deployment layer.

## Configuration

`ExecutionService.from_env()` reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| OPENGATE_URL | http://127.0.0.1:4096 | OpenCode Serve URL |
| OPENGATE_TIMEOUT_SECONDS | 180 | SDK transport timeout in seconds |
| OPENGATE_USERNAME | empty | Basic Auth username |
| OPENGATE_PASSWORD | empty | Basic Auth password |

`OPENGATE_TIMEOUT`, `OPENCODE_USERNAME`, and `OPENCODE_PASSWORD` remain
compatible legacy aliases.

`ExecutionService.from_env(base_url=...)` explicitly overrides the URL from
`OPENGATE_URL`; the CLI `--url` option uses the same configuration path.

Credentials must not be stored in Git, prompts, diagnostics, or batch results.

## Minimal Python API

~~~python
import asyncio

from opengate import ExecutionRequest, ExecutionService


async def main() -> None:
    async with ExecutionService.from_env() as service:
        result = await service.execute(
            ExecutionRequest(
                prompt="Верни JSON с тремя ключевыми фактами о тексте.",
                task="fast-extraction",
            )
        )

    print(result.text)
    print("model:", result.model)
    print("attempts:", len(result.attempts))
    print("latency:", result.total_latency)


asyncio.run(main())
~~~

`ExecutionResult` contains `text`, the final `model`, ordered `attempts`,
`ttft`, `total_latency`, the catalog snapshot used, an optional session,
metadata, and a parsed structured value when the consumer supplies
`response_schema`.

### Sequential semantic iterations and strict JSON

The project owns the context from the previous iteration. OpenGate provides a
transport session and validates the result:

~~~python
from opengate import ExecutionRequest, ExecutionService, SessionHandle

schema = {
    "type": "object",
    "required": ["assignments"],
    "properties": {"assignments": {"type": "array"}},
}

async with ExecutionService.from_env() as service:
    first = await service.execute(ExecutionRequest(
        prompt="Разметь первый batch.", response_schema=schema, keep_session=True,
    ))
    second = await service.execute(ExecutionRequest(
        prompt="Продолжи по следующему batch и сохрани смысл предыдущей итерации.",
        response_schema=schema, session_mode="continue", session=first.session,
        keep_session=True,
    ))
~~~

A violated JSON contract raises `ExecutionError(kind="invalid_response")`.
`diagnostic.source_data` retains the error path and schema, but not credentials.
OpenGate does not interpret schema fields or know the domain semantics of
groups.

The supported JSON Schema subset is explicit: `type`, `enum`, `required`,
`properties`, `items`, and `additionalProperties`, including boolean
subschemas `true` and `false`. Unsupported keywords are rejected before the
transport is called. Invalid JSON, including `NaN` and `Infinity`, returns
`invalid_response`. `enum` comparison respects JSON types: `true` is not equal
to `1`, while numeric values `1` and `1.0` are equivalent.

## Model policy

The default is `free-first`: free candidates are tried first, followed by
compatible paid candidates.

| billing_mode | Behavior |
| --- | --- |
| free-first | free candidates, then paid candidates |
| free-only | free candidates only |
| paid-only | paid candidates only |
| strict-model | only the explicitly named model |

~~~python
request = ExecutionRequest(
    prompt="Сделай глубокий анализ.",
    model="opencode-go/qwen3.7-max",
    billing_mode="strict-model",
)
~~~

If an MCP agent interprets a request as requiring a paid model, it passes the
allowed policy through `ExecutionRequest`. The module does not infer billing
intent from arbitrary prompt text.

Known free identities:

~~~text
opencode/big-pickle
opencode/mimo-v2.5-free
opencode/ling-3.0-flash-fin-free
opencode/nemotron-3-ultra-free
opencode/nemotron-3.5-lightning-free
opencode/muse-spark-1.3-contributor-free
~~~

The catalog is advisory. If a known free identity is absent from the snapshot,
it may still be tried with a real invocation.

## Presets and routing

Available presets:

~~~text
fast-extraction
bulk-classification
long-document-classification
deep-analysis
synthesis
million-synthesis
multimodal
~~~

A preset defines context, reasoning, and attachment requirements, as well as
ordering within its priority group. Free candidates always precede paid ones;
transport health and measured speed cannot change that order. Within a priority
group, candidates with a reachable transport come first. Runtime latency is
used only after three successful calls.

## Async batch

`execute_batch()` accepts strings, mappings, or `BatchItem` values and returns
results through an async iterator:

~~~python
from opengate import ExecutionRequest, ExecutionService


async def process(items: list[str]) -> None:
    async with ExecutionService.from_env() as service:
        request = ExecutionRequest("", task="bulk-classification")

        async for item in service.execute_batch(request, items):
            if item.outcome == "succeeded":
                save_result(item.index, item.result.text)
            elif item.outcome == "not_started":
                record_not_started(item.index)
            else:
                record_error(item.index, item.error)
~~~

Batch uses a default concurrency of 2, requests the catalog once per Run, and
returns results as they complete. When the full free-plus-paid plan is
exhausted, new Items receive `outcome="not_started"` and
`kind="plan_exhausted"`; active Attempts finish under their existing timeout.
This does not mean that a quota was exhausted.

If the consumer closes or cancels the async iterator, OpenGate cancels
unfinished local worker tasks, including the calls they are performing. This
is different from plan exhaustion: exhaustion stops only the scheduling of new
Items, while already active Attempts continue.

Batch v1 does not read old output as a hidden checkpoint and does not support
automatic cross-Run resume. The consumer saves `ItemResult` values and chooses
which Items to submit explicitly in a later Run.

## Sessions

By default, every Attempt gets a new OpenCode session. Pass a `SessionHandle`
to continue one explicitly:

~~~python
from opengate import ExecutionRequest, SessionHandle


request = ExecutionRequest(
    prompt="Продолжи предыдущий анализ.",
    model="opencode/big-pickle",
    billing_mode="strict-model",
    session_mode="continue",
    session=SessionHandle("session-id-from-previous-result"),
)
~~~

A session ID is provider metadata, not project task identity. Durable task
state, idempotency, and preservation of business results belong to the project.

## Errors

Errors are raised as `ExecutionError`. The main kinds are:

~~~text
quota_exhausted
rate_limited
model_unavailable
gateway_unavailable
plan_exhausted
internal_error
request_rejected
invalid_response
invalid_input
~~~

~~~python
from opengate import ExecutionError


try:
    result = await service.execute(request)
except ExecutionError as error:
    print(error.kind)
    print(error.diagnostic)
    print("attempts:", len(error.attempts))
~~~

`quota_exhausted` switches to another candidate and stores the quota TTL in
service memory. `model_unavailable` switches immediately. Rate limits receive
bounded backoff. `ExecutionTransport` adapters must translate external and
backend failures into typed `ExecutionError` values. Only an adapter-raised
`ExecutionError(kind="gateway_unavailable")` during invocation and an
ExecutionService timeout are retried up to two times on the same model. An
unexpected raw exception from an adapter becomes
`ExecutionError(kind="internal_error")` and is neither retried nor routed to
another model. `plan_exhausted` means the Run has no remaining candidate; it
does not mean the provider reported quota exhaustion. Rejected requests,
invalid responses, and invalid input are not retried automatically.

Full prompts and credentials are not included in diagnostics. SDK retries are
disabled, so each retry and model switch is visible in an Attempt.

## CLI

~~~powershell
opengate execute "Return exactly OK" --task fast-extraction
opengate execute "Use paid model" --model opencode-go/qwen3.7-max --billing-mode strict-model
opengate batch input.jsonl output.jsonl --field text --task bulk-classification
~~~

The CLI batch command writes results to `output`, but that file does not become
a checkpoint and is not used to skip rows implicitly on a later run. The CLI
rejects input and output paths that refer to the same file to prevent
truncating the input.

## MCP and the project boundary

An MCP scaffold is useful when an agent needs project-domain tools:

~~~python
from opengate.mcp_runtime import LoopbackAPI, create_mcp_server


class ProjectAdapter:
    name = "My Project"
    instructions = "Use project tools through the local API."

    def register_tools(self, mcp, api: LoopbackAPI) -> None:
        @mcp.tool()
        async def project_get_context() -> dict:
            return await api.acall("GET", "/api/internal/mcp/context")


if __name__ == "__main__":
    create_mcp_server(ProjectAdapter()).run("stdio")
~~~

For remote read-only MCP, a project may explicitly enable HTTPS and a bearer
token:

~~~python
create_mcp_server(
    ProjectAdapter(),
    api_url="https://efir.example/internal",
    allow_remote=True,
    bearer_token="...",
)
~~~

`allow_remote=True` accepts HTTPS only; loopback-only mode remains the default.
Responsibilities are divided as follows:

- the project owns prompts, source data, domain operations, validation, and durable tasks;
- `ExecutionService` owns the catalog, candidate plan, sessions, retries, and diagnostics;
- the MCP adapter provides safe loopback integration between the agent and the project.

Detailed contract: [docs/protocol.md](docs/protocol.md).
Migration map: [docs/migration.md](docs/migration.md).
