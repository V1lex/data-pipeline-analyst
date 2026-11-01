from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
    category: QuestionCategory = Field(description="Категория уточнения")
    question: str = Field(description="Текст уточняющего вопроса")
    suggestion: str | None = Field(
        default=None,
        description="Предложение или вариант по умолчанию от аналитика (для согласования)",
    )


class AnalystResponse(BaseModel):
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
