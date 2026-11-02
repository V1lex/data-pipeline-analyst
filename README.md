# Data Pipeline Analyst

Локальный MVP: диалог с Gemini → согласованная версия спецификации → формальная проверка TLA+/TLC → генерация и скачивание PySpark.

Ветки команды объединены с сохранением истории. Рабочая интеграция предназначена для `dev`; `main` остаётся стабильной базой.

```text
backend/
  src/
    api/                 FastAPI, HTTP-контракты
    agent/analyst/       диалог, полнота требований, согласование параметров
    agent/workflow.py    сохранение диалога и создание версий
    compiler/formal_check/  TLA+, TLC, нормализованный результат
    validator/           общий семантический валидатор
    pipeline_spec/       единый контракт PipelineSpecification
    pipeline_generator/ LLM-генерация и PostgreSQL-репозиторий
    db/                  SQLAlchemy-модели и запуск Alembic
    main.py
  alembic/               первая миграция + совместимая миграция MVP
  pyproject.toml
worker/src/runner.py     независимый процесс очереди FOR UPDATE SKIP LOCKED
frontend/               React + React Flow, Vite, nginx
infra/docker-compose.yml
scripts/download_tlc.py
```

Вложенные пакеты аналитика и формального движка сохранены для понятной связи с работой авторов. Все импорты имеют единую идентичность: `agent.analyst`, `compiler.formal_check`, `pipeline_spec`, `pipeline_generator`.

## Быстрый запуск в Docker

Нужны Docker Compose и Python для загрузки проверенного TLC. Настоящие ключи хранятся только в локальном `.env`.

```sh
cp -n .env.example .env
python3 scripts/download_tlc.py
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

В `.env` укажите `GEMINI_API_KEY` и доступную вам `GEMINI_MODEL`. Compose использует `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` для внутреннего подключения; `DATABASE_URL` нужен при локальном запуске. Backend применяет Alembic, проходит healthcheck, затем запускается worker. Данные PostgreSQL остаются в именованном volume.

- Интерфейс: <http://localhost:3000>
- OpenAPI: <http://localhost:8000/docs>
- Проверка готовности БД и API: <http://localhost:8000/api/v1/health>

Остановка из корня проекта (сохранённая БД остаётся в Docker volume):

```sh
docker compose --env-file .env -f infra/docker-compose.yml down
```

Compose-файл лежит в `infra`, поэтому команда `docker compose down` без `-f` из корня его не найдёт. Локальные `uvicorn` и Vite, запущенные вне Docker, останавливаются через `Ctrl+C` в своих терминалах; Compose ими не управляет.

## Разработка без Docker

Python 3.12+, PostgreSQL и Java 8+; Spark требуется только для отдельных демонстраций. Python-зависимости принадлежат `backend/pyproject.toml`, корень — uv workspace.

```sh
uv sync --extra dev
uv run python scripts/download_tlc.py
# DATABASE_URL и DATABASE_SCHEMA берутся из корневого .env
uv run python -m db.migrations
uv run uvicorn api.app:app --host 127.0.0.1 --port 8000
```

В отдельных терминалах:

```sh
uv run python -m worker.src.runner
npm ci --prefix frontend
npm run dev --prefix frontend
```

Vite открывается на <http://127.0.0.1:5173> и проксирует `/api` на порт 8000. При недоступности uv можно создать Python venv и установить `pip install -e './backend[dev]'`.

## Новый разговорный интерфейс

Основной экран — диалог. Первый отправленный запрос создаёт проект; последние диалоги доступны в боковой истории. «Схема» и «Файлы» открывают контекстные панели со спецификацией, согласованием, проверкой и артефактами выбранной версии. После перезагрузки открывается последний выбранный диалог. Меню `⋯` у чата позволяет переименовать его или удалить с подтверждением. Удаление убирает сообщения, все версии, результаты проверок и файлы этого чата; остальные диалоги сохраняются.

Профиль пока один: статичный `user`, без аккаунтов и входа. Авторизация остаётся отдельной задачей. Дизайн и подтверждение пользователя: `frontend/src/workspace-v4/landing.json`; принятый макет — `layout/full-page-v2.png`.

Для автоматического браузерного сценария без обращения к Gemini нужны отдельная тестовая PostgreSQL, Java/TLC и dev-зависимости. В трёх терминалах:

```sh
PYTHONPATH=. TEST_DATABASE_URL='postgresql://user:password@localhost/test_database' uv run python tests/browser_preview.py
DPA_API_TARGET=http://127.0.0.1:8001 npm run dev --prefix frontend -- --port 5174
FRONTEND_E2E_URL=http://127.0.0.1:5174 PLAYWRIGHT_CHANNEL=chrome npm run test:e2e --prefix frontend
```

QA API работает только на localhost, использует изолированную случайную схему с удалением при штатной остановке и подставленные ответы модели. PostgreSQL, очередь, API и Java/TLC настоящие. Это не проверка доступности Gemini или качества сгенерированного им PySpark. `PLAYWRIGHT_CHANNEL=chrome` использует установленный Chrome; без него установите Chromium через `npx playwright install chromium` из frontend. Обычная сборка приложения никогда не включает этот QA API.

## Сценарий

1. Создайте проект, опишите задачу и ответьте на вопросы аналитика. Новые предложения требуют согласования; неполный или некорректный черновик не считается готовым.
2. Создайте спецификацию. Интерфейс показывает граф и полный JSON. Согласуйте конкретную версию.
3. Запустите TLC. Worker сохранит полный результат; интерфейс обновляется автоматически и показывает контрпример при нарушении.
4. После общего `PROPERTY_HOLDS` получите PySpark. Код и текст создаёт LLM; файл сохраняется за этой версией и доступен после перезапуска. Сетевой повтор использует тот же UUID, явная новая попытка — новый.
5. Изменения требований создают новую версию с отдельным согласованием и проверкой. Старые спецификации и выданные файлы не перезаписываются.

TLC проверяет конечную модель процесса, включая каждое `reachable`-свойство. Это не доказательство корректности сгенерированного Python; сервер его не исполняет.

## Проверки

```sh
uv run pytest -q
uv run ruff check backend/src tests examples worker scripts
npm test --prefix frontend
npm run build --prefix frontend
docker compose --env-file .env.example -f infra/docker-compose.yml config --quiet
```

Для полной проверки используйте отдельную тестовую PostgreSQL с правом создания схем:

```sh
TEST_DATABASE_URL='postgresql://user:password@localhost/test_database' uv run pytest -q
```

Тесты создают случайные схемы и удаляют только их. Без `TEST_DATABASE_URL` проверки БД пропускаются. Реальные TLC-тесты требуют скачанный jar и Java. Spark-примеры запускаются отдельно с `RUN_SPARK_TESTS=1` и дополнением `backend[spark]`.

Фактическая проверка интеграции 01.11.2026: **216 passed, 7 skipped** с настоящими PostgreSQL и Java/TLC; сборка интерфейса, конфигурация Compose и установка wheel с миграциями проверены. Docker daemon на машине не запущен: полный контейнерный старт не проверялся. Реальный Gemini вернул HTTP 400 из-за региона; API/генератор проверены с подставленными ответами, доступ модели остаётся внешней проверкой.

## Границы текущего MVP

Сервис рассчитан на доверенную локальную разработку. Пользовательская авторизация и изоляция проектов ещё не реализованы. Жёсткое завершение worker может оставить задание `RUNNING`; автоматическое восстановление с lease/heartbeat — работа следующего спринта. Для повторной проверки можно создать новый запуск через API/UI. Нагрузочные ограничения и выполнение именно полученного от Gemini PySpark также остаются отдельными задачами.

Единственный владелец схемы — Alembic. Миграция сохраняет данные первой версии Вани, но не выдумывает отсутствующие контексты и результаты старых проверок. Неверсионированная БД старого генератора отклоняется до DDL; её данные нужно переносить явно после резервного копирования. См. [интеграционный контракт](docs/integration.md), [ревью веток](docs/review-integration.md) и [подробности формального движка](docs/review-formal.md).
