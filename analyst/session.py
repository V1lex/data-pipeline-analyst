from __future__ import annotations

import logging
from typing import Any

from analyst.completeness import (
    AgreementStatus,
    CompletenessChecker,
    CompletenessReport,
    DraftParameter,
    ParameterOrigin,
)
from analyst.llm_client import BaseLLMClient, GeminiLLMClient, MockLLMClient
from analyst.models import (
    AnalystResponse,
    ChatMessage,
    ClarificationQuestion,
    QuestionCategory,
    Role,
)
from analyst.prompts import ANALYST_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)


class AnalystSession:
    """Manages dialogue history and interaction with the LLM analyst,

    enforcing programmatic completeness checks and suggestion approvals.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient | None = None,
        system_instruction: str = ANALYST_SYSTEM_INSTRUCTION,
        checker: CompletenessChecker | None = None,
    ) -> None:
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            try:
                self.llm_client = GeminiLLMClient()
            except Exception as e:
                logger.warning(
                    f"Failed to initialize GeminiLLMClient ({e}). Falling back to MockLLMClient."
                )
                self.llm_client = MockLLMClient()

        self.system_instruction = system_instruction
        self.checker = checker or CompletenessChecker()
        self.history: list[ChatMessage] = []
        self.last_response: AnalystResponse | None = None

        # Draft specification state
        self.current_draft: dict[str, Any] = {}
        self.parameters_metadata: list[DraftParameter] = []

    def send_message(self, user_input: str) -> AnalystResponse:
        """Appends user message to history, requests response from LLM,

        and verifies completeness via deterministic rules.
        """
        user_text = user_input.strip()
        user_msg = ChatMessage(role=Role.USER, content=user_text)
        self.history.append(user_msg)

        # Check if user agreed to pending suggestions
        self._check_user_agreement(user_text)

        try:
            response = self.llm_client.generate_response(
                messages=self.history,
                system_instruction=self.system_instruction,
            )
        except Exception as e:
            logger.error(f"Error during LLM call: {e}")
            raise

        # Track any new suggestions proposed by LLM
        self._register_llm_suggestions(response)

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
                            category=QuestionCategory(self._map_op_to_category(missing.operation_type)),
                            question=f"[{missing.step_name}] {missing.question}",
                            suggestion=f"[Предложение аналитика]: {missing.suggestion}",
                        )
                    )

            # If there are pending unconfirmed suggestions, inform the user
            if report.pending_suggestions:
                pending_names = [f"'{p.field_name}' ({p.value})" for p in report.pending_suggestions]
                response.message += (
                    f"\n\nПожалуйста, подтвердите следующие предложения аналитика: {', '.join(pending_names)}."
                )

        self.last_response = response

        # Add model reply to history
        model_msg = ChatMessage(role=Role.MODEL, content=response.message)
        self.history.append(model_msg)

        return response

    def set_draft(self, draft: dict[str, Any]) -> None:
        """Sets the working draft pipeline for deterministic parameter inspection."""
        self.current_draft = draft

    def add_parameter(
        self,
        step_id: str,
        field_name: str,
        value: Any,
        origin: ParameterOrigin,
        status: AgreementStatus = AgreementStatus.PENDING,
        description: str | None = None,
    ) -> None:
        """Explicitly tracks a parameter and its origin (user requirement vs agent suggestion)."""
        param = DraftParameter(
            step_id=step_id,
            field_name=field_name,
            value=value,
            origin=origin,
            status=status,
            description=description,
        )
        self.parameters_metadata.append(param)

    def approve_all_pending_suggestions(self) -> None:
        """Marks all pending agent suggestions as agreed by the user."""
        for param in self.parameters_metadata:
            if param.status == AgreementStatus.PENDING:
                param.status = AgreementStatus.AGREED

    def get_completeness_report(self) -> CompletenessReport:
        """Evaluates draft completeness using deterministic rule checker."""
        if not self.current_draft and not self.parameters_metadata:
            return CompletenessReport(
                is_complete=True,
                missing_parameters=[],
                pending_suggestions=[],
                confirmed_parameters=[],
                summary_message="Черновик еще не сформирован.",
            )
        return self.checker.check_draft(self.current_draft, self.parameters_metadata)

    def reset(self) -> None:
        """Clears conversation history, draft, and parameter state."""
        self.history.clear()
        self.last_response = None
        self.current_draft.clear()
        self.parameters_metadata.clear()

    def get_history(self) -> list[ChatMessage]:
        """Returns a copy of the conversation history."""
        return list(self.history)

    def _check_user_agreement(self, text: str) -> None:
        lower = text.lower()
        agreement_tokens = [
            "согласен",
            "подтверждаю",
            "принимаю",
            "да, подходит",
            "да, используй",
            "ок",
            "хорошо",
            "да",
            "yes",
            "agree",
            "confirm",
        ]
        if any(token in lower for token in agreement_tokens):
            self.approve_all_pending_suggestions()

    def _register_llm_suggestions(self, response: AnalystResponse) -> None:
        existing_keys = {(p.step_id, p.field_name) for p in self.parameters_metadata}
        for q in response.clarification_questions:
            key = ("suggestion", q.category.value)
            if q.suggestion and key not in existing_keys:
                self.add_parameter(
                    step_id="suggestion",
                    field_name=q.category.value,
                    value=q.suggestion,
                    origin=ParameterOrigin.AGENT_SUGGESTION,
                    status=AgreementStatus.PENDING,
                    description=q.question,
                )
                existing_keys.add(key)

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
