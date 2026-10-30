import json
import re
import time
from importlib.resources import files

from pydantic import ValidationError

from .errors import PipelineError
from .llm import LlmClient
from .models import GeneratedCode, ModelPayload, VerifiedContext, ensure_verified

DISCLAIMER = (
    "Проверена модель процесса; корректность сгенерированного кода автоматически не проверялась."
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class PySparkGenerator:
    """Calls an injected LLM; forwards its human answer and code without operation templates."""

    def __init__(self, client: LlmClient, *, max_attempts: int = 2, retry_delay: float = 1.0):
        if not 1 <= max_attempts <= 3 or not 0 <= retry_delay <= 30:
            raise ValueError("Use 1–3 attempts and a retry delay between 0 and 30 seconds")
        self.client = client
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    def generate(self, context: VerifiedContext) -> GeneratedCode:
        ensure_verified(context)
        system = files("pipeline_generator").joinpath("generation_prompt.md").read_text("utf-8")
        user = context.model_dump_json()
        for attempt in range(self.max_attempts):
            try:
                completion = self.client.complete(system, user, ModelPayload.model_json_schema())
                break
            except PipelineError as error:
                if not error.retryable or attempt + 1 >= self.max_attempts:
                    raise
                time.sleep(self.retry_delay)
        if completion.finish_reason == "MAX_TOKENS":
            raise PipelineError(
                "LLM_TRUNCATED", "Ответ обрезан: увеличьте лимит вывода и повторите явно."
            )
        if completion.finish_reason != "STOP":
            raise PipelineError("LLM_BLOCKED", "Модель не завершила допустимый ответ.")
        try:
            if len(completion.text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise PipelineError("LLM_INVALID_RESPONSE", "Ответ превышает допустимый размер.")
            raw = json.loads(completion.text, object_pairs_hook=_unique_keys)
            payload = ModelPayload.model_validate(raw)
            # JSON permits escaped lone surrogates; PostgreSQL/text files require valid UTF-8.
            json.dumps(payload.model_dump(), ensure_ascii=False).encode("utf-8")
        except (ValueError, ValidationError, TypeError, RecursionError):
            raise PipelineError(
                "LLM_INVALID_RESPONSE", "Модель вернула некорректный JSON-ответ."
            ) from None
        if not payload.answer.strip():
            raise PipelineError("LLM_INVALID_RESPONSE", "Модель не вернула текстовый ответ.")
        if payload.status == "NEEDS_CLARIFICATION":
            if not payload.questions or any(not question.strip() for question in payload.questions):
                raise PipelineError(
                    "LLM_INVALID_RESPONSE", "В ответе на уточнение отсутствуют вопросы."
                )
            raise PipelineError(
                "NEEDS_CLARIFICATION",
                "Требования нужно уточнить.",
                details={"answer": payload.answer, "questions": payload.questions},
            )
        if not payload.content.strip():
            raise PipelineError("EMPTY_CODE", "Модель не вернула код.")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.py", payload.filename)
            or payload.questions
        ):
            raise PipelineError(
                "LLM_INVALID_RESPONSE", "Некорректное имя файла или неоднозначный ответ."
            )
        return GeneratedCode(
            answer=payload.answer,
            filename=payload.filename,
            content=payload.content,
            requirements=payload.requirements,
            notes=[*payload.notes, DISCLAIMER],
            provider=completion.provider,
            model=completion.model,
        )
