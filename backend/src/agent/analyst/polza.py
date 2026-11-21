"""Polza chat-completions client shared by analyst, builder and generator."""

import json
import os

import httpx
from pipeline_generator.errors import PipelineError
from pipeline_generator.models import Completion
from pydantic import ValidationError

from agent.analyst.llm_client import BaseLLMClient
from agent.analyst.models import AnalystResponse, ChatMessage

DEFAULT_POLZA_MODEL = "deepseek/deepseek-v4.1-flash-1017"


class PolzaLLMClient(BaseLLMClient):
    def __init__(self, api_key=None, model_name=None, client=None):
        self.api_key = api_key if api_key is not None else os.getenv("POLZA_API_KEY", "")
        if not self.api_key.strip():
            raise ValueError("POLZA_API_KEY is not configured")
        self.model_name = model_name or os.getenv("POLZA_MODEL", DEFAULT_POLZA_MODEL)
        self.client = client or httpx.Client(timeout=float(os.getenv("POLZA_TIMEOUT", "60")))

    def _request(self, messages):
        try:
            response = self.client.post(
                "https://polza.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model_name,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192")),
                },
            )
        except httpx.TimeoutException:
            raise PipelineError(
                "LLM_TIMEOUT", "Polza AI не ответил вовремя.", retryable=True
            ) from None
        except httpx.TransportError:
            raise PipelineError(
                "LLM_TRANSIENT_ERROR", "Ошибка соединения с Polza AI.", retryable=True
            ) from None
        if response.status_code == 402:
            raise PipelineError("LLM_QUOTA_EXCEEDED", "Проверьте баланс или лимит ключа Polza AI.")
        if response.status_code in (401, 403):
            raise PipelineError("LLM_AUTH_ERROR", "Polza AI отклонил ключ или доступ.")
        if response.status_code == 429 or response.status_code >= 500:
            raise PipelineError(
                "LLM_TRANSIENT_ERROR",
                "Polza AI временно недоступен или исчерпана квота.",
                retryable=True,
            )
        if response.is_error:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Polza AI отклонил запрос или настройки модели."
            )
        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            reason = choice["finish_reason"]
            if not isinstance(text, str) or not text.strip() or not isinstance(reason, str):
                raise ValueError
            return text, reason
        except (ValueError, TypeError, KeyError, IndexError):
            raise PipelineError(
                "LLM_INVALID_RESPONSE", "Polza AI вернул некорректный ответ."
            ) from None

    def generate_response(
        self, messages: list[ChatMessage], system_instruction: str
    ) -> AnalystResponse:
        conversation = [{"role": "system", "content": system_instruction}]
        conversation.extend(
            {"role": "assistant" if msg.role == "model" else str(msg.role), "content": msg.content}
            for msg in messages
        )
        for attempt in range(3):
            text, reason = self._request(conversation)
            self._check_finish(reason)
            try:
                return AnalystResponse.model_validate_json(text)
            except ValidationError as error:
                if attempt == 2:
                    break
                problems = [
                    {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
                    for item in error.errors()[:20]
                ]
                conversation.extend(
                    [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                "Предыдущий ответ не прошёл проверку формата. Исправь только формат и верни полный JSON. "
                                "Допустимые категории вопросов: sources, fields, formula, business_rules, sinks, general. "
                                "draft должен содержать массивы sources, steps, sinks с объектами и строковыми id; "
                                "operation каждого шага — объект с полем type, а не строка. "
                                "Ошибки полей: " + json.dumps(problems, ensure_ascii=False)
                            ),
                        },
                    ]
                )
        raise PipelineError(
            "LLM_INVALID_RESPONSE",
            "Модель не смогла сформировать корректный ответ. Повторите запрос.",
        ) from None

    @staticmethod
    def _check_finish(reason):
        if reason != "stop":
            raise PipelineError(
                "LLM_TRUNCATED" if reason == "length" else "LLM_BLOCKED",
                "Модель не завершила ответ.",
            )

    def generate_json(self, system, prompt):
        text, reason = self._request(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        )
        self._check_finish(reason)
        return text

    def complete(self, system, user, response_schema):
        text, reason = self._request(
            [
                {
                    "role": "system",
                    "content": system
                    + "\nВерни JSON по схеме: "
                    + json.dumps(response_schema, ensure_ascii=False),
                },
                {"role": "user", "content": user},
            ]
        )
        return Completion(
            text=text,
            finish_reason={"stop": "STOP", "length": "MAX_TOKENS"}.get(reason, reason.upper()),
            provider="polza",
            model=self.model_name,
        )

    def close(self):
        self.client.close()
