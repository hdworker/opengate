# OpenGate MCP

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

Preset задаёт требования к context, reasoning, attachments и первичный порядок.
Runtime latency влияет на порядок только после минимум трёх наблюдений и
остаётся вторичной метрикой.

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
плана новые Items получают not_started.

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
backoff. Gateway/timeout повторяется до двух раз на той же модели. Rejected
request, invalid response и invalid input автоматически не повторяются.

Full prompt и credentials не входят в diagnostics. SDK retry отключён, поэтому
каждый retry и model switch виден в Attempt.

## CLI

~~~powershell
opengate execute "Return exactly OK" --task fast-extraction
opengate execute "Use paid model" --model opencode-go/qwen3.7-max --billing-mode strict-model
opengate batch input.jsonl output.jsonl --field text --task bulk-classification
~~~

CLI batch записывает результаты в output, но этот файл не становится checkpoint и
не используется для неявного пропуска строк при следующем запуске.

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
