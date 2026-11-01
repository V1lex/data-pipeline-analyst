"""QA ONLY: injected model responses, real temporary PostgreSQL and TLC.
Never imported by production app. State is an isolated schema, removed on shutdown.
"""

import os
import threading
from uuid import uuid4

import psycopg
import uvicorn
from agent.analyst.specification_builder import MockSpecificationBuilder
from api.app import create_app
from pipeline_generator.storage import PostgresRepository
from psycopg import sql
from test_api import CodeModel, Model

from worker.src.runner import process_one

url = os.environ["TEST_DATABASE_URL"]
schema = "qa_chat_" + uuid4().hex
with psycopg.connect(url, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
repository = PostgresRepository(url, schema=schema)
repository.migrate()
builder = MockSpecificationBuilder()


class QAAnalyst(Model):
    def generate_response(self, messages, **kwargs):
        if "ошибка" in messages[-1].content.lower():
            raise RuntimeError("QA provider failure")
        return super().generate_response(messages=messages, **kwargs)


model = QAAnalyst(builder.build_verifiable_specification([]))
app = create_app(
    repository=repository, analyst_client=model, builder=builder, generation_client=CodeModel()
)
stop = threading.Event()


def worker():
    while not stop.is_set():
        if not process_one(repository):
            stop.wait(0.2)


thread = threading.Thread(target=worker, daemon=True)
thread.start()
try:
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.getenv("BROWSER_QA_PORT", "8001")), log_level="warning"
    )
finally:
    stop.set()
    thread.join(timeout=35)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
