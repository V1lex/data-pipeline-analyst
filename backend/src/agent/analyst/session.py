from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any

from agent.analyst.completeness import (
    AgreementStatus,
    CompletenessChecker,
    CompletenessReport,
    DraftParameter,
    ParameterOrigin,
    SessionState,
)
from agent.analyst.config import USE_MOCK_CLIENT
from agent.analyst.llm_client import BaseLLMClient, GeminiLLMClient, MockLLMClient
from agent.analyst.models import (
    AnalystResponse,
    ChatMessage,
    ClarificationQuestion,
    QuestionCategory,
    Role,
)
from agent.analyst.prompts import ANALYST_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)


class AnalystSession:
    """Manages dialogue history and interaction with the LLM analyst,
    enforcing programmatic completeness checks, revision tracking, and explicit suggestion approvals.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient | None = None,
        system_instruction: str = ANALYST_SYSTEM_INSTRUCTION,
        checker: CompletenessChecker | None = None,
    ) -> None:
        use_mock = USE_MOCK_CLIENT or os.getenv("USE_MOCK_CLIENT", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        if llm_client is not None:
            self.llm_client = llm_client
        elif use_mock:
            self.llm_client = MockLLMClient()
        else:
            # Explicit initialization: do not silently mask errors with MockLLMClient (A09)
            self.llm_client = GeminiLLMClient()

        self.system_instruction = system_instruction
        self.checker = checker or CompletenessChecker()
        self.history: list[ChatMessage] = []
        self.last_response: AnalystResponse | None = None

        # Draft specification state
        self.current_draft: dict[str, Any] = {}
        self.parameters_metadata: list[DraftParameter] = []
        self.state: SessionState = SessionState.INSUFFICIENT_DATA

    def snapshot(self) -> dict:
        return {
            "history": [m.model_dump(mode="json") for m in self.history],
            "last_response": self.last_response.model_dump(mode="json")
            if self.last_response
            else None,
            "current_draft": copy.deepcopy(self.current_draft),
            "parameters_metadata": [p.model_dump(mode="json") for p in self.parameters_metadata],
            "state": self.state.value,
        }

    @classmethod
    def restore(cls, snapshot: dict, *, llm_client):
        session = cls(llm_client=llm_client)
        session._restore_snapshot(snapshot)
        return session

    def _restore_snapshot(self, snapshot):
        self.history = [ChatMessage.model_validate(m) for m in snapshot.get("history", [])]
        response = snapshot.get("last_response")
        self.last_response = AnalystResponse.model_validate(response) if response else None
        self.current_draft = copy.deepcopy(snapshot.get("current_draft", {}))
        self.parameters_metadata = [
            DraftParameter.model_validate(p) for p in snapshot.get("parameters_metadata", [])
        ]
        self.state = SessionState(snapshot.get("state", "insufficient_data"))

    def send_message(self, user_input: str) -> AnalystResponse:
        if not user_input.strip():
            raise ValueError("Сообщение не должно быть пустым")
        before = self.snapshot()
        try:
            return self._send_message(user_input)
        except Exception:
            self._restore_snapshot(before)
            raise

    def _send_message(self, user_input: str) -> AnalystResponse:
        """Appends user message to history, requests response from LLM,
        syncs draft requirements, and verifies completeness via deterministic rules.
        """
        user_text = user_input.strip()
        user_msg = ChatMessage(role=Role.USER, content=user_text)
        self.history.append(user_msg)

        # Check if user agreed or rejected pending suggestions (A02)
        self._check_user_agreement(user_text)

        try:
            response = self.llm_client.generate_response(
                messages=self.history,
                system_instruction=self.system_instruction
                + "\nТекущий черновик и согласование: "
                + json.dumps(
                    {
                        "draft": self.current_draft,
                        "parameters": [p.model_dump(mode="json") for p in self.parameters_metadata],
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.error(f"Error during LLM call: {e}")
            raise

        # Sync current_draft from dialogue state and confirmed parameters (A04)
        self._sync_draft_from_dialogue(response)
        self._reconcile_parameters(response)
        self._register_llm_suggestions(response)
        self._apply_agreed_parameters()
        response.draft = copy.deepcopy(self.current_draft) if self.current_draft else None

        # Programmatic completeness verification
        report = self.get_completeness_report()
        if not report.is_complete:
            # Enforce that agent cannot complete specification if mandatory parameters are missing
            response.is_complete = False

            # Inject missing parameters into clarification questions if not already asked
            existing_questions = {q.question for q in response.clarification_questions}
            for missing in report.missing_parameters:
                if missing.question not in existing_questions:
                    response.clarification_questions.append(
                        ClarificationQuestion(
                            category=QuestionCategory(
                                self._map_op_to_category(missing.operation_type)
                            ),
                            question=f"[{missing.step_id}] {missing.question}",
                            step_id=missing.step_id,
                            field_name=missing.field_name,
                            suggestion=f"[Предложение аналитика]: {missing.suggestion}",
                        )
                    )

            # If there are pending unconfirmed suggestions, inform the user
            if report.pending_suggestions:
                pending_names = [
                    f"'{p.field_name}' ({p.value})" for p in report.pending_suggestions
                ]
                response.message += f"\n\nПожалуйста, подтвердите следующие предложения аналитика: {', '.join(pending_names)}."

        self.last_response = response
        self.state = self.get_state()

        # Add model reply to history
        model_msg = ChatMessage(role=Role.MODEL, content=response.message)
        self.history.append(model_msg)

        return response

    def set_draft(self, draft: dict[str, Any]) -> None:
        """Sets the working draft pipeline for deterministic parameter inspection."""
        self.current_draft = copy.deepcopy(draft)

    def add_parameter(
        self,
        step_id: str,
        field_name: str,
        value: Any,
        origin: ParameterOrigin,
        status: AgreementStatus = AgreementStatus.PENDING,
        description: str | None = None,
        revision: int = 1,
        param_id: str | None = None,
    ) -> DraftParameter:
        """Explicitly tracks a parameter and its origin (user requirement vs agent suggestion)."""
        pid = param_id or f"{step_id}:{field_name}"
        param = DraftParameter(
            step_id=step_id,
            field_name=field_name,
            value=value,
            origin=origin,
            status=status,
            description=description,
            revision=revision,
            param_id=pid,
        )
        self.parameters_metadata.append(param)
        return param

    def approve_parameter(self, param_id_or_field: str, revision: int | None = None) -> bool:
        """Explicitly approves a parameter by param_id, field_name, step_id:field_name, or step_id.
        Returns False if the parameter is not found or if a stale revision is specified.
        """
        for param in self.parameters_metadata:
            target_matches = (
                param.param_id == param_id_or_field
                or param.field_name == param_id_or_field
                or f"{param.step_id}:{param.field_name}" == param_id_or_field
                or param.step_id == param_id_or_field
            )
            if target_matches:
                if revision is not None and param.revision != revision:
                    logger.warning(
                        f"Approval rejected for stale revision {revision} (current is {param.revision})."
                    )
                    return False
                param.status = AgreementStatus.AGREED
                return True
        return False

    def reject_parameter(self, param_id_or_field: str, revision: int | None = None) -> bool:
        """Explicitly marks a parameter as REJECTED."""
        for param in self.parameters_metadata:
            target_matches = (
                param.param_id == param_id_or_field
                or param.field_name == param_id_or_field
                or f"{param.step_id}:{param.field_name}" == param_id_or_field
                or param.step_id == param_id_or_field
            )
            if target_matches:
                if revision is not None and param.revision != revision:
                    return False
                param.status = AgreementStatus.REJECTED
                return True
        return False

    def approve_all_pending_suggestions(self, revision: int | None = None) -> None:
        """Marks all pending agent suggestions as agreed by the user (respecting revision if provided)."""
        for param in self.parameters_metadata:
            if param.status == AgreementStatus.PENDING:
                if revision is None or param.revision == revision:
                    param.status = AgreementStatus.AGREED

    def reject_all_pending_suggestions(self, revision: int | None = None) -> None:
        """Marks all pending agent suggestions as rejected."""
        for param in self.parameters_metadata:
            if param.status == AgreementStatus.PENDING:
                if revision is None or param.revision == revision:
                    param.status = AgreementStatus.REJECTED

    def get_completeness_report(self) -> CompletenessReport:
        """Evaluates draft completeness using deterministic rule checker."""
        if not self.current_draft and not self.parameters_metadata:
            return CompletenessReport(
                is_complete=False,
                missing_parameters=[],
                pending_suggestions=[],
                confirmed_parameters=[],
                summary_message="Черновик спецификации еще не сформирован.",
            )
        return self.checker.check_draft(self.current_draft, self.parameters_metadata)

    def get_state(self) -> SessionState:
        """Returns the current state in the analyst workflow."""
        report = self.get_completeness_report()
        if not self.current_draft and not self.parameters_metadata:
            return SessionState.INSUFFICIENT_DATA
        if report.pending_suggestions:
            return SessionState.AWAITING_APPROVAL
        if report.is_complete:
            return SessionState.DRAFT_COMPLETE
        return SessionState.INSUFFICIENT_DATA

    def reset(self) -> None:
        """Clears conversation history, draft, and parameter state."""
        self.history.clear()
        self.last_response = None
        self.current_draft.clear()
        self.parameters_metadata.clear()
        self.state = SessionState.INSUFFICIENT_DATA

    def get_history(self) -> list[ChatMessage]:
        """Returns a copy of the conversation history."""
        return list(self.history)

    def _check_user_agreement(self, text: str) -> None:
        """Parses user input for explicit confirmation or rejection of suggestions.
        Avoids false-positive approvals on negations, substring tokens, and neutral text (A02).
        """
        trimmed = text.strip()
        if not trimmed:
            return

        lower = trimmed.lower()

        # 1. Targeted CLI rejection: "reject <id>", "отклоняю <id>", "не принимаю <id>", "не согласен с <id>", "не подтверждаю <id>"
        m_rej = re.match(
            r"^(?:reject|отклоняю|отклонить|не\s+принимаю|не\s+подтверждаю|не\s+согласен\s+с)\s+([a-zA-Z0-9_:\-]+)(?:\s+(?:rev|ред|v)\s*(\d+))?$",
            lower,
        )
        if m_rej:
            target_id = m_rej.group(1)
            rev = int(m_rej.group(2)) if m_rej.group(2) else None
            self.reject_parameter(target_id, revision=rev)
            return

        # 2. General Negations / Rejections: evaluated FIRST so negations NEVER approve!
        negation_pattern = r"\b(не\s+согласен|не\s+подтверждаю|не\s+принимай|не\s+используй|не\s+надо|не\s+хочу|не\s+подходит|отклоняю|отклонить|против|нет|disagree|reject)\b"
        if re.search(negation_pattern, lower):
            general_refusal = (
                r"(?:reject\s+all|отклонить\s+все|отклоняю\s+все|"
                r"не\s+согласен\s+со\s+всеми|не\s+подтверждаю\s+все|"
                r"^(?:нет|не\s+согласен|не\s+подтверждаю|не\s+принимаю|отклоняю|отклонить|no|disagree)[.!]*(?:\s+.*)?$)"
            )
            if re.search(general_refusal, lower):
                self.reject_all_pending_suggestions()
            return

        # 3. Explicit CLI commands for all (ONLY reached if NO negation is present):
        if re.search(r"\b(?:approve\s+all|подтверждаю\s+все|согласен\s+со\s+всеми)\b", lower):
            self.approve_all_pending_suggestions()
            return

        # 4. Targeted CLI commands: "approve <id> [rev <num>]" or "подтверждаю <id>"
        m_app = re.match(
            r"^(?:approve|подтверждаю|принимаю)\s+([a-zA-Z0-9_:\-]+)(?:\s+(?:rev|ред|v)\s*(\d+))?$",
            lower,
        )
        if m_app:
            target_id = m_app.group(1)
            rev = int(m_app.group(2)) if m_app.group(2) else None
            self.approve_parameter(target_id, revision=rev)
            return

        # 5. Standalone affirmations without negation
        affirmation_pattern = (
            r"^(?:да|ок|согласен|подтверждаю|принимаю|подходит|хорошо|"
            r"да,\s*(?:я\s*)?(?:согласен|подтверждаю|принимаю|подходит|используй)|"
            r"yes|agree|confirm|ok)[.!]?$"
        )
        if re.match(affirmation_pattern, lower):
            self.approve_all_pending_suggestions()
            return

        # 6. Natural affirmative phrases
        if lower.startswith(
            (
                "да, согласен",
                "да, я согласен",
                "да, подтверждаю",
                "да, я подтверждаю",
                "да, принимаю",
                "да, подходит",
                "согласен с предложенным",
                "подтверждаю предложен",
            )
        ):
            self.approve_all_pending_suggestions()
            return

    def _register_llm_suggestions(self, response: AnalystResponse) -> None:
        """Registers suggestions from LLM clarification questions.
        Tracks concrete values and revisions per step and field.
        A changed value resets status to PENDING and increments revision (A05).
        """
        for q in response.clarification_questions:
            if not q.suggestion or (q.field_name is not None and q.value is None):
                # A diagnostic/default description is a question, not a concrete value.
                continue

            # Extract step_id if encoded in question "[step_id] ..."
            step_match = re.match(r"^\[([a-zA-Z0-9_\-]+)\]", q.question.strip())
            step_id = q.step_id or (
                step_match.group(1) if step_match else f"step_{q.category.value}"
            )
            field_name = q.field_name or q.category.value
            param_id = f"{step_id}:{field_name}"

            # Check if this parameter already exists in metadata
            existing = next(
                (
                    p
                    for p in self.parameters_metadata
                    if p.param_id == param_id
                    or (p.step_id == step_id and p.field_name == field_name)
                ),
                None,
            )

            if existing is None:
                self.add_parameter(
                    step_id=step_id,
                    field_name=field_name,
                    value=q.value if q.value is not None else q.suggestion,
                    origin=ParameterOrigin.AGENT_SUGGESTION,
                    status=AgreementStatus.PENDING,
                    description=q.question,
                    revision=1,
                    param_id=param_id,
                )
            else:
                # Value changed: create new revision, reset agreement to PENDING
                if existing.value != (q.value if q.value is not None else q.suggestion):
                    existing.value = q.value if q.value is not None else q.suggestion
                    existing.status = AgreementStatus.PENDING
                    existing.origin = ParameterOrigin.AGENT_SUGGESTION
                    existing.revision += 1
                    existing.description = q.question
                # If value is unchanged, preserve existing status and revision

    def _parameter_target(self, step_id: str) -> dict[str, Any] | None:
        for collection in ("steps", "sources", "sinks"):
            for item in self.current_draft.get(collection, []):
                if isinstance(item, dict) and item.get("id") == step_id:
                    return item.get("operation", {}) if collection == "steps" else item
        return None

    def _reconcile_parameters(self, response: AnalystResponse) -> None:
        if response.draft is not None:
            target_ids = {
                item["id"]
                for key in ("sources", "steps", "sinks")
                for item in self.current_draft.get(key, [])
            }
            self.parameters_metadata = [
                p for p in self.parameters_metadata if p.step_id in target_ids
            ]
        for update in response.parameter_updates:
            expected_id = f"{update.step_id}:{update.field_name}"
            if update.param_id != expected_id or self._parameter_target(update.step_id) is None:
                raise ValueError(f"Unknown parameter target: {expected_id}")
            if (
                not update.field_name
                or update.field_name.startswith("_")
                or update.field_name in ("id", "type")
            ):
                raise ValueError(f"Invalid parameter field: {expected_id}")
            existing = next(
                (p for p in self.parameters_metadata if p.param_id == expected_id), None
            )
            if existing and update.revision < existing.revision:
                raise ValueError(f"Stale parameter revision: {expected_id}")
            param = update.model_copy(deep=True)
            param.status = (
                AgreementStatus.AGREED
                if param.origin == ParameterOrigin.USER_EXPLICIT
                else AgreementStatus.PENDING
            )
            if existing:
                param.revision = max(
                    update.revision, existing.revision + (existing.value != update.value)
                )
                # Repeating the same suggestion cannot revoke an existing user decision.
                if (
                    param.origin == ParameterOrigin.AGENT_SUGGESTION
                    and existing.value == param.value
                    and update.revision == existing.revision
                ):
                    param.status = existing.status
                self.parameters_metadata[self.parameters_metadata.index(existing)] = param
            else:
                self.parameters_metadata.append(param)

    def _apply_agreed_parameters(self) -> None:
        for param in self.parameters_metadata:
            if param.status != AgreementStatus.AGREED:
                continue
            target = self._parameter_target(param.step_id)
            if target is None or "." in param.field_name:
                continue
            # Category-only legacy suggestions are conversational text, not operation fields.
            if param.field_name in {category.value for category in QuestionCategory}:
                continue
            target[param.field_name] = copy.deepcopy(param.value)

    def _sync_draft_from_dialogue(self, response: AnalystResponse) -> None:
        """Synchronizes structured current_draft from dialogue state, operations, and confirmed parameters (A04)."""
        if response.draft is not None:
            candidate = copy.deepcopy(response.draft)
            for key in ("sources", "steps", "sinks"):
                if key not in candidate or not isinstance(candidate[key], list):
                    raise ValueError(f"draft.{key} must be a list")
                ids = [item.get("id") for item in candidate[key] if isinstance(item, dict)]
                if (
                    len(ids) != len(candidate[key])
                    or any(not i for i in ids)
                    or len(ids) != len(set(ids))
                ):
                    raise ValueError(f"draft.{key} requires unique stable IDs")
            self.current_draft = candidate
            return
        if (
            not self.current_draft
            and not response.identified_operations
            and not response.identified_sources
        ):
            return

        if "sources" not in self.current_draft:
            self.current_draft["sources"] = []
        if "steps" not in self.current_draft:
            self.current_draft["steps"] = []
        if "sinks" not in self.current_draft:
            self.current_draft["sinks"] = []

        existing_sources = {
            s.get("id") for s in self.current_draft["sources"] if isinstance(s, dict)
        }
        for src in response.identified_sources:
            if src not in existing_sources:
                self.current_draft["sources"].append({"id": src, "name": src})
                existing_sources.add(src)

        existing_steps = [s.get("id") for s in self.current_draft["steps"] if isinstance(s, dict)]
        existing_ops = [
            s.get("operation", {}).get("type")
            for s in self.current_draft["steps"]
            if isinstance(s, dict) and isinstance(s.get("operation"), dict)
        ]
        for idx, op in enumerate(response.identified_operations, start=1):
            step_id = f"step_{op}_{idx}"
            if op not in existing_ops and step_id not in existing_steps:
                self.current_draft["steps"].append(
                    {
                        "id": step_id,
                        "name": f"Step {op}",
                        "operation": {"type": op},
                        "inputs": [],
                        "depends_on": [],
                    }
                )
                existing_ops.append(op)
                existing_steps.append(step_id)

    def _map_op_to_category(self, op_type: Any) -> str:
        s = str(op_type).lower()
        if "read" in s:
            return "sources"
        if "write" in s:
            return "sinks"
        if "filter" in s:
            return "business_rules"
        if "with_columns" in s:
            return "formula"
        return "fields"
