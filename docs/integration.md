# Подключение к backend и worker

Модуль реализует T23–T25 как Python API. Он не создаёт параллельный FastAPI backend, маршруты авторизации или LLM-аналитика.

## Установка и конфигурация

```sh
uv sync --extra dev
cp -n .env.example .env
# Заполните конфигурацию локально и экспортируйте переменные в окружение процесса.
```

Backend может использовать свой существующий загрузчик конфигурации. `GeminiClient.from_environment(env_file=".env")` явно читает локальный файл, не исполняя его; переменные процесса имеют приоритет. Без `env_file` читается только окружение процесса. Ключ Gemini не передаётся в контексте модели и не сохраняется в PostgreSQL. `DATABASE_SCHEMA` по умолчанию — `public`; для новой отдельной схемы её заранее создаёт владелец БД. Миграция предназначена для пустой согласованной схемы, запускать её один раз:

```python
import os
from pipeline_generator.storage import PostgresRepository

repository = PostgresRepository(os.environ["DATABASE_URL"],
                                schema=os.getenv("DATABASE_SCHEMA", "public"))
repository.migrate()
```

Два дополнения исходной картинки: `specifications.context_json` и `verification_runs.result_json`. Состояния и типы артефактов перечислены в `migrations/001_initial.sql`; их требуется сопоставить с миграциями backend до объединения веток, если там появится другой вариант.

## Создание версии

```python
project_id = repository.create_project("Выручка по городам")
message_id = repository.add_message(project_id, "user", "Согласованный текст запроса")
# behavioral_model сформирован аналитиком, без generator-specific DSL.
spec_id = repository.create_specification(
    project_id, behavioral_model, approved_requirements,
    [message_id], {"pyspark_version": "3.5.8", "extra_dependencies": []},
)
# Только после явного согласования пользователем:
repository.approve_specification(spec_id)
run_id = repository.enqueue_verification(spec_id)
```

Снимок выбранных сообщений фиксируется в момент создания версии. При новых решениях создаётся следующая версия; нельзя редактировать прежние `spec_json/context_json`. Индекс и короткая блокировка проекта обеспечивают уникальную последовательную нумерацию даже при конкурентном создании.

## Worker

```python
run = repository.claim_verification()  # FOR UPDATE SKIP LOCKED
if run is not None:
    spec = repository.get_specification(run["spec_id"])
    # Здесь вызываются Validator → compiler → SANY/TLC → parser Кирилла.
    normalized_result = checker.verify(spec["spec_json"])
    repository.finish_verification(run["id"], normalized_result)
```

`checker.verify` — обозначение точки подключения, такого готового метода в просмотренной ветке Кирилла нет. При последней проверке ветки `tla_tlc` на коммите `7065373` уже появился `parser.parse_tlc_output(stdout, variables) -> str` с JSON-строкой. Код выше объясняет итоговый контракт worker, не является заявлением о сквозном запуске TLC: обёртка должна десериализовать ответ и гарантировать завершённую проверку всех свойств, включая reachable. В текущих compiler/runner для reachable ещё есть TODO; один progress вывод не является подтверждением завершения. `RunnerOutput.status='success'` не принимается вместо общего нормализованного ответа. Писать результат может доверенный worker, а не публичный клиент.

`list_specifications(project_id)`, `list_verifications(spec_id)`, `list_property_results(run_id)` возвращают историю. Полный ответ и диагностика всегда находятся в `verification_runs.result_json`. Отрицательный ответ не создаёт результатов для неизвестных свойств.

## Генерация и скачивание

```python
from uuid import UUID
from pipeline_generator.gemini import GeminiClient
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.service import GenerationService

client = GeminiClient.from_environment(env_file=".env")
try:
    service = GenerationService(repository, PySparkGenerator(client))
    # Backend сначала проверяет право текущего пользователя на проект/spec_id.
    result = service.generate(spec_id, request_id=UUID(request_id_from_backend))
    api_body = result.model_dump(mode="json")
    # UI показывает api_body["answer"] как текст LLM, api_body["content"] — как код.
finally:
    client.close()

# Backend повторно проверяет доступ к проекту при скачивании:
artifact = repository.get_artifact(spec_id, result.artifact_id)
# Content-Type: text/x-python; charset=utf-8
# Content-Disposition: attachment; filename="<artifact.filename>"
download_bytes = artifact.content.encode("utf-8")
```

И текст `answer`, и код `content` создаются LLM. `PySparkGenerator` — обвязка вызова: передаёт контекст, разбирает ответ и не собирает PySpark из шаблонов. Текст не добавляется в скачиваемый `.py`.

`request_id` создаётся backend один раз на пользовательское действие и повторно используется при сетевом повторе. Новый UUID — явная новая генерация. Имена выданных файлов включают UUID: существующие результаты не затираются. Ошибки доступны как `PipelineError.to_dict()` и не меняют результат TLC.

Сервис синхронный: асинхронный backend должен вызывать его через существующий thread pool или background job, не блокируя event loop. Общий аккаунт Gemini должен использовать согласованное ограничение запросов backend; этот модуль не добавляет второй независимый rate limiter. Повторы временного сбоя ограничены максимум тремя попытками (по умолчанию две); платного/model fallback нет.

Во время обращения к LLM соединение с PostgreSQL не удерживается. При аварии процесса manifest может остаться RUNNING; после истечения срока попытки клиент получает `GENERATION_EXPIRED`. Для повтора нужен новый UUID — автоматическое восстановление неизвестного внешнего вызова намеренно не выполняется.

## Проверки

```sh
uv run pytest -q
TEST_DATABASE_URL='postgresql://.../disposable_database' uv run pytest -m postgres -q
uv run ruff check src tests examples
```

PostgreSQL-тесты создают собственную случайную схему и удаляют только её; без `TEST_DATABASE_URL` они отмечаются skipped. Используйте выделенную тестовую БД с правом создавать схемы.

Модуль предоставляет проверенный сервисный интерфейс. Подключение к настоящим API/UI Вани и парсеру Кирилла остаётся командной интеграцией и не считается выполненным только по fake-клиенту.

## Документация провайдера

- [Gemini generateContent и причины завершения](https://ai.google.dev/api/generate-content)
- [Структурированный JSON-ответ](https://ai.google.dev/gemini-api/docs/structured-output)
- [Доступные модели](https://ai.google.dev/gemini-api/docs/models)
- [Тарифы и доступность free tier](https://ai.google.dev/gemini-api/docs/pricing)

Версии и квоты выбранной модели проверяются в конкретном проекте AI Studio; модуль не включает платный fallback.
