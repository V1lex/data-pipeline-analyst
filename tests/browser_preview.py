"""QA ONLY: injected model responses, real temporary PostgreSQL and TLC.
Never imported by production app. State is an isolated schema, removed on shutdown.
"""

import json
import os
import threading
from uuid import uuid4

import psycopg
import uvicorn
from agent.analyst.models import AnalystResponse, Role
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


class QABuilder(MockSpecificationBuilder):
    def build_verifiable_specification(self, history):
        spec = super().build_verifiable_specification(history)
        if any(
            message.role == Role.USER and "нарушение" in message.content.lower()
            for message in history
        ):
            spec.formal_model["variables"].append({"name": "data_valid", "type": "boolean"})
            spec.formal_model["initial"]["data_valid"] = False
            spec.formal_model["properties"] = [
                {
                    "id": "Safety",
                    "type": "invariant",
                    "expression": {
                        "or": [
                            {"ne": [{"var": "status"}, "READ"]},
                            {"eq": [{"var": "data_valid"}, True]},
                        ]
                    },
                }
            ]
        return spec


builder = QABuilder()


class QAAnalyst(Model):
    def generate_response(self, messages, **kwargs):
        if "ошибка" in messages[-1].content.lower():
            raise RuntimeError("QA provider failure")
        if any(
            message.role == Role.USER and "нарушение" in message.content.lower()
            for message in messages
        ):
            spec = builder.build_verifiable_specification(messages)
            return AnalystResponse(
                message="Требования собраны. Проверьте спецификацию.",
                is_complete=True,
                draft=spec.model_dump(mode="json", by_alias=True),
            )
        return super().generate_response(messages=messages, **kwargs)

    def generate_json(self, system, prompt):
        data = json.loads(prompt)
        transitions = data["formal_model"]["transitions"]
        transitions[0]["effects"].append({"set": ["data_valid", True]})
        return json.dumps(
            {
                "issue_summary": "Данные не проверены перед состоянием READ",
                "proposed_change": "Добавить проверку данных в переход Read",
                "rationale": "Согласовать переход с ограничением Safety",
                "patch_data": {"transitions": transitions},
            }
        )


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
