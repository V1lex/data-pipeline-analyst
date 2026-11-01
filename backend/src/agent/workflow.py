"""Persistent, leased dialogue workflow; no database transaction surrounds an LLM call."""

import json

from pipeline_generator.errors import PipelineError

from agent.analyst.models import ChatMessage, Role
from agent.analyst.session import AnalystSession


class AnalystWorkflow:
    def __init__(self, repository, client, builder):
        self.repository = repository
        self.client = client
        self.builder = builder

    def send(self, project_id, content):
        lease, snapshot = self.repository.begin_dialogue(project_id)
        try:
            session = AnalystSession.restore(snapshot, llm_client=self.client)
            response = session.send_message(content)
            self.repository.commit_dialogue(
                project_id, lease, session.snapshot(), user=content, assistant=response.message
            )
            return {
                "response": response.model_dump(mode="json"),
                "state": session.state,
                "completeness": session.get_completeness_report().model_dump(mode="json"),
            }
        finally:
            self.repository.abort_dialogue(project_id, lease)

    def build(self, project_id):
        lease, snapshot = self.repository.begin_dialogue(project_id)
        try:
            session = AnalystSession.restore(snapshot, llm_client=self.client)
            report = session.get_completeness_report()
            if (
                not report.is_complete
                or not session.last_response
                or not session.last_response.is_complete
            ):
                raise PipelineError(
                    "REQUIREMENTS_INCOMPLETE",
                    "Ответьте на вопросы аналитика и согласуйте предложения перед созданием версии.",
                )
            agreed = {
                "draft": session.current_draft,
                "parameters": [p.model_dump(mode="json") for p in report.confirmed_parameters],
            }
            history = session.get_history() + [
                ChatMessage(
                    role=Role.SYSTEM,
                    content="Актуальные согласованные параметры. Сохрани значения, ID шагов и формулы: "
                    + json.dumps(agreed, ensure_ascii=False),
                )
            ]
            messages = self.repository.list_messages(project_id)
            spec = self.builder.build_verifiable_specification(history)
            from pipeline_spec.models import PipelineSpecification

            raw = dict(session.current_draft)
            raw.setdefault("version", "1.0.0")
            raw.setdefault("metadata", {"id": "draft", "name": "Draft"})
            approved = PipelineSpecification.model_validate(raw).model_dump(
                mode="json", by_alias=True
            )
            actual = spec.model_dump(mode="json", by_alias=True)
            protected = ["sources", "steps", "sinks"]
            if "constraints" in session.current_draft:
                protected.append("constraints")
            if any(actual[field] != approved[field] for field in protected):
                raise PipelineError(
                    "LLM_INVALID_RESPONSE",
                    "Модель изменила согласованные параметры. Создайте спецификацию повторно; требования сохранены.",
                )
            requirements = "\n".join(m["content"] for m in messages if m["role"] == "user")
            spec_id = self.repository.create_specification(
                project_id,
                spec,
                requirements,
                [m["id"] for m in messages],
                {"pyspark_version": "3.5.8", "extra_dependencies": []},
                dialogue_token=lease,
            )
            return self.repository.get_specification(spec_id)
        finally:
            self.repository.abort_dialogue(project_id, lease)
