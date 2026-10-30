from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from google import genai
from google.genai import types

from analyst.config import DEFAULT_MODEL, DEFAULT_TEMPERATURE, GEMINI_API_KEY
from analyst.models import (
    AnalystResponse,
    ChatMessage,
    ClarificationQuestion,
    QuestionCategory,
    Role,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        key = api_key or GEMINI_API_KEY
        if not key:
            raise ValueError("GEMINI_API_KEY is not provided and not found in environment.")
        self.client = genai.Client(api_key=key)
        self.model_name = model_name
        self.temperature = temperature

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

        if not response.text:
            raise RuntimeError("Gemini returned empty response text.")

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
