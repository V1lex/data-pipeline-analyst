from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from pipeline_spec.models.enums import OperationType


class ParameterOrigin(StrEnum):
    USER_EXPLICIT = "user_explicit"  # Явно введено пользователем
    AGENT_SUGGESTION = "agent_suggestion"  # Предложено агентом (требует подтверждения)


class AgreementStatus(StrEnum):
    AGREED = "agreed"  # Согласовано с пользователем
    PENDING = "pending"  # Ожидает подтверждения пользователя
    REJECTED = "rejected"  # Отклонено


class SessionState(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"  # Недостаточно данных для черновика
    AWAITING_APPROVAL = "awaiting_approval"  # Черновик есть, но есть неподтвержденные предложения
    DRAFT_COMPLETE = "draft_complete"        # Черновик полностью заполнен и все предложения согласованы
    SUBMITTED_FOR_VERIFICATION = "submitted_for_verification"  # Передан на верификацию


class DraftParameter(BaseModel):
    step_id: str = Field(description="ID шага, к которому относится параметр")
    field_name: str = Field(description="Имя параметра")
    value: Any = Field(description="Значение параметра")
    origin: ParameterOrigin = Field(
        description="Происхождение: явно от пользователя или предложение агента"
    )
    status: AgreementStatus = Field(
        default=AgreementStatus.PENDING,
        description="Статус согласования с пользователем",
    )
    description: str | None = Field(
        default=None,
        description="Пояснение смысла параметра",
    )
    revision: int = Field(
        default=1,
        description="Номер редакции предложенного значения",
    )
    param_id: str | None = Field(
        default=None,
        description="Уникальный идентификатор параметра",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.param_id:
            self.param_id = f"{self.step_id}:{self.field_name}"


class MissingParameter(BaseModel):
    step_id: str = Field(description="ID шага с недостающим параметром")
    step_name: str = Field(description="Название шага")
    operation_type: OperationType = Field(description="Тип операции")
    field_name: str = Field(description="Имя недостающего параметра")
    question: str = Field(description="Уточняющий вопрос пользователю")
    suggestion: str = Field(description="Предложение по умолчанию от агента")


class CompletenessReport(BaseModel):
    is_complete: bool = Field(
        description="True, если все обязательные параметры заполнены и все предложения агента согласованы"
    )
    missing_parameters: list[MissingParameter] = Field(
        default_factory=list,
        description="Список отсутствующих обязательных параметров",
    )
    pending_suggestions: list[DraftParameter] = Field(
        default_factory=list,
        description="Предложения агента, ожидающие подтверждения пользователя",
    )
    confirmed_parameters: list[DraftParameter] = Field(
        default_factory=list,
        description="Параметры, явно подтвержденные или заданные пользователем",
    )
    summary_message: str = Field(
        description="Человекочитаемое резюме готовности спецификации"
    )
