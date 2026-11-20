from __future__ import annotations

import copy
import json
import logging
from typing import Any

from pipeline_spec.models import PipelineSpecification
from pydantic import BaseModel, Field

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
    base_revision: int = Field(
        default=1,
        description="Номер редакции спецификации, для которой сформировано предложение",
    )


class VerificationExplanation(BaseModel):
    """Human-readable explanation of formal verification result in domain terms."""

    status: str = Field(
        description="Статус верификации (PROPERTY_HOLDS, PROPERTY_VIOLATED, PROPERTY_UNREACHABLE, MODEL_INVALID, TIMEOUT и др.)"
    )
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
        spec_id: str | None = None,
        revision: int | None = None,
    ) -> VerificationExplanation:
        """Explains verification result strictly relying on TLC output.
        Accepts objects, dictionaries, or JSON strings.
        Does not fabricate counterexamples or hide unresolved results (A08).
        """
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {"status": "UNKNOWN", "raw_output": result}

        def _get(obj: Any, key: str, default: Any = None) -> Any:
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        status = _get(result, "status")
        status_str = str(status) if status else "UNKNOWN"
        pipeline_name = specification.metadata.name
        pipeline_id = spec_id or specification.metadata.id
        current_rev = revision or 1

        # Case 1: All properties hold
        if status_str == "PROPERTY_HOLDS":
            stats = _get(result, "statistics")
            stats_summary = ""
            if stats:
                depth = _get(stats, "depth", "неизвестна")
                states = _get(stats, "states_generated", "неизвестно")
                distinct = _get(stats, "distinct_states", "неизвестно")
                stats_summary = f"Исследовано состояний: {states} (уникальных: {distinct}, глубина графа: {depth}). "

            return VerificationExplanation(
                status="PROPERTY_HOLDS",
                title="✓ Формальная верификация пройдена успешно!",
                summary=(
                    f"Модель-чекер TLC завершил проверку модели конвейера '{pipeline_name}' "
                    f"(ID: {pipeline_id}, ред. {current_rev}) при заданных допущениях. "
                    f"{stats_summary}"
                    f"Обратите внимание: формальная проверка модели подтверждает логику переходов состояний, "
                    f"но не доказывает корректность вычислений PySpark и качество данных в реальных таблицах."
                ),
                trace_steps=[],
                proposal=None,
                requires_reverification=False,
            )

        if status_str == "PROPERTY_UNREACHABLE":
            prop_id = _get(result, "property_id") or "UnknownProperty"
            return VerificationExplanation(
                status="PROPERTY_UNREACHABLE",
                title=f"⚠ Состояние недостижимо: {prop_id}",
                summary=(
                    f"Свойство достижимости '{prop_id}' не выполнено: целевое состояние не найдено "
                    f"ни в одном состоянии полностью исследованного графа."
                ),
                trace_steps=[],
                proposal=None,
                requires_reverification=True,
            )

        # Case 2: Property violated
        if status_str == "PROPERTY_VIOLATED":
            prop_id = _get(result, "property_id") or "UnknownProperty"
            ce = _get(result, "counterexample")
            raw_steps = _get(ce, "steps", []) or []

            trace_steps: list[str] = []
            for s in raw_steps:
                s_num = _get(s, "number", 0)
                trans = _get(s, "transition", None) or (
                    "Начальное состояние (Init)" if s_num == 0 else "переход не указан"
                )
                state = _get(s, "state", {})
                state_str = ", ".join(f"{k} = {v}" for k, v in state.items())
                trace_steps.append(f"Шаг {s_num} [{trans}]: {state_str}")

            formal_model = specification.formal_model or {}
            properties = _get(formal_model, "properties", []) or []
            prop = next((p for p in properties if _get(p, "id") == prop_id), None)
            kind = _get(prop, "type")
            descriptions = {
                "invariant": "инварианта",
                "forbidden_state": "запрета состояния",
                "reachable": "достижимости",
                "deadlock_free": "отсутствия блокировок",
            }
            issue_detail = (
                f"Обнаружено нарушение свойства {descriptions.get(kind, 'модели')} '{prop_id}'."
            )
            trace_summary = (
                f"TLC сохранил контрпример из {len(trace_steps)} шагов."
                if trace_steps
                else "Контрпример отсутствует."
            )
            summary = f"{issue_detail} Спецификация '{pipeline_name}' (ID: {pipeline_id}, ред. {current_rev}). {trace_summary}"

            proposal = self._generate_revision_proposal(
                prop_id, raw_steps, specification, base_revision=current_rev
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
            raw_errors = _get(result, "errors", []) or []
            err_details = [
                f"- [{_get(e, 'location', 'unknown')}]: {_get(e, 'message', str(e))}"
                for e in raw_errors
            ]
            return VerificationExplanation(
                status="MODEL_INVALID",
                title="✕ Спецификация содержит семантические ошибки",
                summary=(
                    f"Валидатор формальной модели обнаружил {len(raw_errors)} ошибок в '{pipeline_name}':\n"
                    + "\n".join(err_details)
                ),
                trace_steps=[],
                proposal=RevisionProposal(
                    issue_summary=f"Ошибки структуры формальной модели ({len(raw_errors)})",
                    proposed_change="Исправить некорректные типы/ссылки в initial или transitions",
                    rationale="Необходимо для прохождения семантической валидации TLA+",
                    is_agreed=False,
                    base_revision=current_rev,
                ),
                requires_reverification=True,
            )

        # Case 4: Timeout, State Limit, or Infrastructure Error (A08)
        return VerificationExplanation(
            status=status_str,
            title="⚠ Неустановленный результат верификации",
            summary=(
                f"Формальная проверка спецификации '{pipeline_name}' завершилась со статусом '{status_str}'. "
                f"Результат проверки не установлен (превышен таймаут выполнения, исчерпан лимит состояний или произошла ошибка среды TLC). "
                f"Модуль не утверждает корректность или некорректность пайплайна."
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
        current_revision: int = 1,
        bump_schema_version: bool = True,
    ) -> PipelineSpecification:
        """Applies an agreed revision proposal, validating actual difference and semantic correctness (A07).

        Raises ValueError if proposal is not agreed, stale, empty, or a no-op.
        """
        if not proposal.is_agreed:
            raise ValueError(
                "Невозможно применить изменение: предложение аналитика еще не согласовано пользователем."
            )

        if proposal.base_revision is not None and proposal.base_revision != current_revision:
            raise ValueError(
                f"Предложение устарело (stale revision): создано для редакции {proposal.base_revision}, "
                f"а текущая редакция — {current_revision}."
            )

        spec_data = copy.deepcopy(specification.model_dump(by_alias=True, mode="python"))
        patch = copy.deepcopy(proposal.patch_data)

        if not patch:
            raise ValueError(
                "Правка пуста (empty patch): требуется явно заданное структурированное изменение."
            )

        # Apply patch data
        if "formal_model" in patch and spec_data.get("formal_model"):
            spec_data["formal_model"].update(patch["formal_model"])

        if "transitions" in patch and spec_data.get("formal_model"):
            spec_data["formal_model"]["transitions"] = patch["transitions"]

        if "properties" in patch and spec_data.get("formal_model"):
            spec_data["formal_model"]["properties"] = patch["properties"]

        if "steps" in patch:
            spec_data["steps"] = patch["steps"]

        # Check whether the content actually changed (A07)
        original_data = specification.model_dump(by_alias=True, mode="python")
        if spec_data == original_data:
            raise ValueError("Правка не изменяет содержимое спецификации (no-op patch).")

        # Format version management: separate schema version from requirements revision (A07 / A13)
        current_version = spec_data.get("version", "1.0.0")
        if next_version:
            spec_data["version"] = next_version
        elif bump_schema_version:
            spec_data["version"] = self._bump_minor_version(current_version)
        else:
            spec_data["version"] = current_version

        # Fully validate new specification
        new_spec = PipelineSpecification.model_validate(spec_data)
        new_spec.get_topological_order()

        logger.info(f"Successfully applied revision for specification '{new_spec.metadata.name}'.")
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
        base_revision: int = 1,
    ) -> RevisionProposal:
        """Describe the observed failure without guessing a model-changing repair."""
        if steps:
            last_step = steps[-1]
            transition = (
                last_step.get("transition")
                if isinstance(last_step, dict)
                else getattr(last_step, "transition", None)
            )
            state = (
                last_step.get("state", {})
                if isinstance(last_step, dict)
                else getattr(last_step, "state", {})
            )
            issue = f"Свойство '{prop_id}' нарушено; последний сохранённый переход: {transition or 'не указан'}, состояние: {state}."
        else:
            issue = f"Свойство '{prop_id}' нарушено; трасса контрпримера отсутствует."
        proposed = "Разобрать нарушенное свойство и контрпример, затем предложить явное изменение модели для согласования."
        rationale = (
            "Результат проверки сам по себе не определяет корректное исправление. "
            "После согласованного изменения требуется повторная проверка TLC."
        )
        patch: dict[str, Any] = {}

        return RevisionProposal(
            issue_summary=issue,
            proposed_change=proposed,
            rationale=rationale,
            target_property_id=prop_id,
            patch_data=patch,
            is_agreed=False,
            base_revision=base_revision,
        )
