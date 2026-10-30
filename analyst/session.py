from __future__ import annotations

import logging
from typing import Any

from analyst.llm_client import BaseLLMClient, GeminiLLMClient, MockLLMClient
from analyst.models import AnalystResponse, ChatMessage, Role
from analyst.prompts import ANALYST_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)


class AnalystSession:
    """Manages dialogue history and interaction with the LLM analyst."""

    def __init__(
        self,
        llm_client: BaseLLMClient | None = None,
        system_instruction: str = ANALYST_SYSTEM_INSTRUCTION,
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
        self.history: list[ChatMessage] = []
        self.last_response: AnalystResponse | None = None

    def send_message(self, user_input: str) -> AnalystResponse:
        """Appends user message to history, requests response from LLM, and records model reply."""
        user_msg = ChatMessage(role=Role.USER, content=user_input.strip())
        self.history.append(user_msg)

        try:
            response = self.llm_client.generate_response(
                messages=self.history,
                system_instruction=self.system_instruction,
            )
        except Exception as e:
            logger.error(f"Error during LLM call: {e}")
            raise

        self.last_response = response

        # Add model reply to history
        model_msg = ChatMessage(role=Role.MODEL, content=response.message)
        self.history.append(model_msg)

        return response

    def reset(self) -> None:
        """Clears conversation history and state."""
        self.history.clear()
        self.last_response = None

    def get_history(self) -> list[ChatMessage]:
        """Returns a copy of the conversation history."""
        return list(self.history)
