from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from analyst.config import DEFAULT_MODEL, DEFAULT_TEMPERATURE, GEMINI_API_KEY, GEMINI_TIMEOUT, MAX_RETRIES
from analyst.models import (
    AnalystResponse,
    ChatMessage,
    ClarificationQuestion,
    QuestionCategory,
    Role,
)

logger = logging.getLogger(__name__)


class LLMTruncatedResponseError(RuntimeError):
    """Raised when model output was truncated (e.g. MAX_TOKENS)."""
    pass


class LLMBlockedResponseError(RuntimeError):
    """Raised when model output was blocked by safety filters."""
    pass


class LLMEmptyResponseError(RuntimeError):
    """Raised when model returns empty text."""
    pass


def _is_transient_error(exception: BaseException) -> bool:
    """Predicate identifying temporary/retryable errors (network, 5xx, rate limits).
    Non-retryable: auth errors, client errors (400, 401, 403, 404), schema/type errors, truncation, safety blocks (A09).
    """
    if isinstance(exception, (ValueError, TypeError, LLMTruncatedResponseError, LLMBlockedResponseError)):
        return False

    code = getattr(exception, "code", None) or getattr(exception, "status_code", None)
    if code in (400, 401, 403, 404):
        return False

    return True


class BaseLLMClient(ABC):
    @abstractmethod
    def generate_response(
        self,
        messages: list[ChatMessage],
        system_instruction: str,
    ) -> AnalystResponse:
        """Generates structured AnalystResponse from conversation history."""
        raise NotImplementedError


class GeminiLLMClient(BaseLLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = GEMINI_TIMEOUT,
        client: genai.Client | None = None,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.timeout = timeout

        if client is not None:
            self.client = client
        else:
            key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
            if not key:
                raise ValueError("GEMINI_API_KEY is not provided and not found in environment.")
            self.client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(timeout=int(timeout)),
            )

    @retry(
        retry=retry_if_exception(_is_transient_error),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        stop=stop_after_attempt(MAX_RETRIES),
        reraise=True,
    )
    def generate_response(
        self,
        messages: list[ChatMessage],
        system_instruction: str,
    ) -> AnalystResponse:
        # Convert internal ChatMessage objects to Gemini contents format
        contents: list[types.Content] = []
        for msg in messages:
            role = "user" if msg.role == Role.USER else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg.content)],
                )
            )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=AnalystResponse,
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        if not response.candidates:
            raise RuntimeError("Gemini returned no candidates.")

        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        finish_str = str(finish_reason).upper() if finish_reason else ""

        if "MAX_TOKENS" in finish_str:
            raise LLMTruncatedResponseError("Gemini response was truncated due to MAX_TOKENS limit.")
        if "SAFETY" in finish_str:
            raise LLMBlockedResponseError("Gemini response was blocked by SAFETY filters.")

        if not response.text:
            raise LLMEmptyResponseError("Gemini returned empty response text.")

        return AnalystResponse.model_validate_json(response.text)


class MockLLMClient(BaseLLMClient):
    """Deterministic mock client for offline tests and CI environments."""

    def generate_response(
        self,
        messages: list[ChatMessage],
        system_instruction: str,
    ) -> AnalystResponse:
        last_message = messages[-1].content.lower() if messages else ""

        # Test criterion: "Посчитай выручку по городам" or other brief queries
        if "посчитай выручку по городам" in last_message or (len(messages) == 1 and len(last_message) < 50):
            return AnalystResponse(
                message=(
                    "Для реализации конвейера расчета выручки по городам мне требуется уточнить ряд ключевых параметров. "
                    "Пожалуйста, ответьте на следующие вопросы или подтвердите предложенные варианты:"
                ),
                is_complete=False,
                identified_sources=[],
                identified_operations=["aggregate", "filter"],
                business_goals="Расчет суммарной выручки с группировкой по городам",
                clarification_questions=[
                    ClarificationQuestion(
                        category=QuestionCategory.SOURCES,
                        question="Где находятся исходные данные и в каком они формате (например, путь к файлам CSV/Parquet или таблица БД)?",
                        suggestion="s3://data-lake/orders/ в формате Parquet",
                    ),
                    ClarificationQuestion(
                        category=QuestionCategory.FIELDS,
                        question="Какие поля содержат город (название колонки) и сумму заказа?",
                        suggestion="Колонки 'city' и 'amount'",
                    ),
                    ClarificationQuestion(
                        category=QuestionCategory.FORMULA,
                        question="Какова точная формула расчета выручки (просто сумма amount, сумма с учетом скидки или за вычетом комиссий)?",
                        suggestion="sum(amount * (1 - discount))",
                    ),
                    ClarificationQuestion(
                        category=QuestionCategory.BUSINESS_RULES,
                        question="Какие заказы нужно учитывать, а какие отфильтровать (например, исключать ли отмененные и возвраты)?",
                        suggestion="Учитывать только заказы со статусом 'COMPLETED'",
                    ),
                    ClarificationQuestion(
                        category=QuestionCategory.SINKS,
                        question="Куда необходимо сохранить готовую витрину (путь/таблица и режим записи)?",
                        suggestion="s3://data-lake/gold/city_revenue/ в режиме overwrite",
                    ),
                ],
            )

        # Full context provided
        return AnalystResponse(
            message="Спасибо за подробные данные! Вся необходимая информация собрана, конвейер готов к формированию спецификации.",
            is_complete=True,
            identified_sources=["orders_table"],
            identified_operations=["read", "filter", "aggregate", "write"],
            business_goals="Расчет выручки по городам с фильтрацией успешных заказов",
            clarification_questions=[],
        )
