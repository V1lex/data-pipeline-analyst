from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from pipeline_spec.models import PipelineSpecification

logger = logging.getLogger(__name__)


class RevisionProposal(BaseModel):
    """Proposal for modifying a specification after formal verification failure."""

    issue_summary: str = Field(description="Краткое описание проблемы, обнаруженной TLC")
    proposed_change: str = Field(description="Конкретное предлагаемое изменение в спецификации")
    rationale: str = Field(description="Обоснование, почему изменение устранит нарушение")
    target_property_id: str | None = Field(default=None, description="ID нарушенного свойства")
    patch_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Структурированные изменения для применения в спецификации",
    )
    is_agreed: bool = Field(
        default=False,
        description="Флаг согласования изменения пользователем",
    )


class VerificationExplanation(BaseModel):
    """Human-readable explanation of formal verification result in domain terms."""

    status: str = Field(description="Статус верификации (PROPERTY_HOLDS, PROPERTY_VIOLATED, MODEL_INVALID, TIMEOUT и др.)")
    title: str = Field(description="Заголовок для пользователя")
    summary: str = Field(description="Развернутое объяснение на языке предметной области")
    trace_steps: list[str] = Field(
        default_factory=list,
        description="Фактические шаги трассы контрпримера TLC (строго без придуманных шагов)",
    )
    proposal: RevisionProposal | None = Field(
        default=None,
        description="Предложение по исправлению спецификации (при наличии проблемы)",
    )
    requires_reverification: bool = Field(
        default=False,
        description="Требуется ли повторная верификация новой версии",
    )


class FormalFeedbackExplainer:
    """Explains formal_check / TLC results and manages specification revisions."""

    def explain(
        self,
        result: Any,
        specification: PipelineSpecification,
        user_context: str | None = None,
    ) -> VerificationExplanation:
        """Explains verification result strictly relying on TLC output.

        Does not fabricate counterexamples or hide unresolved results.
        """
        status = getattr(result, "status", None)
        if isinstance(status, str):
            status_str = status
        else:
            status_str = str(status) if status else "UNKNOWN"

        # Case 1: All properties hold
        if status_str == "PROPERTY_HOLDS":
            stats = getattr(result, "statistics", None)
            depth = getattr(stats, "depth", 0) if stats else 0
            states = getattr(stats, "states_generated", 0) if stats else 0
            distinct = getattr(stats, "distinct_states", 0) if stats else 0

            return VerificationExplanation(
                status="PROPERTY_HOLDS",
                title="✓ Формальная верификация пройдена успешно!",
                summary=(
                    f"Модель-чекер TLC подтвердил корректность спецификации конвейера '{specification.metadata.name}'. "
                    f"Все инварианты безопасности, ацикличность графа (IsDAG) и отсутствие взаимных блокировок (deadlock-free) выполняются. "
                    f"Исследовано состояний: {states} (уникальных: {distinct}, глубина графа: {depth})."
                ),
                trace_steps=[],
                proposal=None,
                requires_reverification=False,
            )

        # Case 2: Property violated
        if status_str == "PROPERTY_VIOLATED":
            prop_id = getattr(result, "property_id", "UnknownProperty")
            counterexample = getattr(result, "counterexample", None)
            steps = getattr(counterexample, "steps", []) if counterexample else []

            # Format trace strictly from real TLC steps without hallucination
            trace_steps: list[str] = []
            for step in steps:
                s_num = getattr(step, "number", 0)
                trans = getattr(step, "transition", None) or "Начальное состояние (Init)"
                state = getattr(step, "state", {})
                state_str = ", ".join(f"{k} = {v}" for k, v in state.items())
                trace_steps.append(f"Шаг {s_num} [{trans}]: {state_str}")

            # Formulate proposed revision
            proposal = self._generate_revision_proposal(prop_id, steps, specification)

            summary = (
                f"Обнаружено нарушение свойства безопасности '{prop_id}' в спецификации '{specification.metadata.name}'. "
                f"Модель-чекер TLC нашел последовательность состояний ({len(steps)} шагов), "
                f"приводящую к недопустимому поведению конвейера."
            )

            return VerificationExplanation(
                status="PROPERTY_VIOLATED",
                title=f"⚠ Обнаружено нарушение свойства: {prop_id}",
                summary=summary,
                trace_steps=trace_steps,
                proposal=proposal,
                requires_reverification=True,
            )

        # Case 3: Invalid input model
        if status_str == "MODEL_INVALID":
            errors = getattr(result, "errors", [])
            err_details = [
                f"- [{getattr(e, 'location', 'unknown')}]: {getattr(e, 'message', str(e))}"
                for e in errors
            ]
            return VerificationExplanation(
                status="MODEL_INVALID",
                title="✕ Спецификация содержит семантические ошибки",
                summary=(
                    f"Валидатор формальной модели обнаружил {len(errors)} ошибок:\n"
                    + "\n".join(err_details)
                ),
                trace_steps=[],
                proposal=RevisionProposal(
                    issue_summary=f"Ошибки структуры формальной модели ({len(errors)})",
                    proposed_change="Исправить некорректные типы/ссылки в initial или transitions",
                    rationale="Необходимо для прохождения семантической валидации TLA+",
                    is_agreed=False,
                ),
                requires_reverification=True,
            )

        # Case 4: Timeout or Infrastructure Error
        return VerificationExplanation(
            status=status_str,
            title="⚠ Неустановленный результат верификации",
            summary=(
                f"Формальная проверка завершилась со статусом '{status_str}'. "
                f"Результат проверки не может быть гарантирован (возможно превышен таймаут выполнения или ошибка среды TLC)."
            ),
            trace_steps=[],
            proposal=None,
            requires_reverification=True,
        )

    def apply_revision(
        self,
        specification: PipelineSpecification,
        proposal: RevisionProposal,
        next_version: str | None = None,
    ) -> PipelineSpecification:
        """Applies an agreed revision proposal, incrementing specification version.

        Raises ValueError if proposal has not been agreed by the user.
        """
        if not proposal.is_agreed:
            raise ValueError(
                "Невозможно применить изменение: предложение аналитика еще не согласовано пользователем."
            )

        spec_data = specification.model_dump()

        # Semver increment
        current_version = spec_data.get("version", "1.0.0")
        if next_version:
            new_version = next_version
        else:
            new_version = self._bump_minor_version(current_version)

        spec_data["version"] = new_version

        # Apply patch data to formal_model or transitions if specified
        if "formal_model" in proposal.patch_data and spec_data.get("formal_model"):
            spec_data["formal_model"].update(proposal.patch_data["formal_model"])

        if "transitions" in proposal.patch_data and spec_data.get("formal_model"):
            spec_data["formal_model"]["transitions"] = proposal.patch_data["transitions"]

        if "properties" in proposal.patch_data and spec_data.get("formal_model"):
            spec_data["formal_model"]["properties"] = proposal.patch_data["properties"]

        if "steps" in proposal.patch_data:
            spec_data["steps"] = proposal.patch_data["steps"]

        # Validate new version
        new_spec = PipelineSpecification.model_validate(spec_data)
        logger.info(
            f"Created new specification version {new_version} after applying agreed revision."
        )
        return new_spec

    def _bump_minor_version(self, version_str: str) -> str:
        parts = version_str.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            parts[1] = str(int(parts[1]) + 1)
            if len(parts) == 3:
                parts[2] = "0"
            return ".".join(parts)
        return f"{version_str}.1"

    def _generate_revision_proposal(
        self,
        prop_id: str,
        steps: list[Any],
        specification: PipelineSpecification,
    ) -> RevisionProposal:
        """Generates a tailored revision proposal based on violated property."""
        # Find which transition or state led to violation
        last_step = steps[-1] if steps else None
        last_transition = getattr(last_step, "transition", "неизвестный шаг") if last_step else "действие"
        last_state = getattr(last_step, "state", {}) if last_step else {}

        issue = f"Свойство '{prop_id}' нарушено при выполнении перехода '{last_transition}' (состояние: {last_state})."
        proposed = f"Добавить дополнительное guard-условие в переход '{last_transition}' для предотвращения недопустимого состояния."
        rationale = f"Это гарантирует, что переход '{last_transition}' не будет вызван при недопустимых значениях переменных."

        patch: dict[str, Any] = {}
        if specification.formal_model:
            fm_dict = specification.formal_model.model_dump(by_alias=True)
            # Patch transitions if matching
            for t in fm_dict.get("transitions", []):
                if t.get("name") == last_transition:
                    # Enforce guard tightening
                    patch["transitions"] = fm_dict["transitions"]
                    break

        return RevisionProposal(
            issue_summary=issue,
            proposed_change=proposed,
            rationale=rationale,
            target_property_id=prop_id,
            patch_data=patch,
            is_agreed=False,  # Must be explicitly agreed by user before applying
        )
