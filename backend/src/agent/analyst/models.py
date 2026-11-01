from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agent.analyst.completeness.models import DraftParameter


class Role(StrEnum):
    USER = "user"
    MODEL = "model"
    SYSTEM = "system"


class QuestionCategory(StrEnum):
    SOURCES = "sources"  # Источники данных, форматы, пути
    FIELDS = "fields"  # Необходимые поля и типы
    FORMULA = "formula"  # Формулы и логика вычислений
    BUSINESS_RULES = "business_rules"  # Фильтры, исключения, условия
    SINKS = "sinks"  # Целевое назначение, формат и режим сохранения
    GENERAL = "general"  # Прочие уточнения


class ChatMessage(BaseModel):
    role: Role
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ClarificationQuestion(BaseModel):
    step_id: str | None = None
    field_name: str | None = None
    value: Any = None
    category: QuestionCategory = Field(description="Категория уточнения")
    question: str = Field(description="Текст уточняющего вопроса")
    suggestion: str | None = Field(
        default=None,
        description="Предложение или вариант по умолчанию от аналитика (для согласования)",
    )


class AnalystResponse(BaseModel):
    parameter_updates: list[DraftParameter] = Field(
        default_factory=list,
        description="Типизированные изменения параметров. Явный ответ пользователя: user_explicit; новое предложение: agent_suggestion. Сохраняй step_id, field_name и текущую revision.",
    )
    draft: dict[str, Any] | None = Field(
        default=None,
        description="Полный актуальный структурированный черновик: sources, steps, sinks. Сохраняй стабильные ID и все явно указанные значения; неизвестное оставляй пустым.",
    )
    message: str = Field(
        description="Текстовый ответ пользователю в вежливом и профессиональном тоне"
    )
    is_complete: bool = Field(
        description="True, если всех данных достаточно для формирования спецификации; False, если требуются уточнения"
    )
    clarification_questions: list[ClarificationQuestion] = Field(
        default_factory=list,
        description="Список уточняющих вопросов, если информации недостаточно",
    )
    identified_sources: list[str] = Field(
        default_factory=list,
        description="Список источников данных, которые удалось идентифицировать из диалога",
    )
    identified_operations: list[str] = Field(
        default_factory=list,
        description="Список операций обработки (read, filter, join, aggregate, write и др.), упомянутых в диалоге",
    )
    business_goals: str | None = Field(
        default=None,
        description="Кратко сформулированная бизнес-цель запроса",
    )
