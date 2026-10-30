"""Use the analyst's existing Google SDK client with the generator's response schema."""

from typing import Any

import httpx

from .errors import PipelineError
from .models import Completion


class SharedGeminiClient:
    def __init__(
        self,
        client: Any,
        model: str,
        *,
        timeout_seconds: float = 60,
        max_output_tokens: int = 8192,
    ):
        if not model or not 0 < timeout_seconds <= 300 or not 256 <= max_output_tokens <= 65536:
            raise PipelineError("CONFIGURATION_ERROR", "Проверьте модель и лимиты Gemini.")
        self.client = client
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    @classmethod
    def from_analyst_client(cls, analyst_client: Any, **options):
        """Borrow the raw SDK client; its owner remains responsible for closing it."""
        try:
            return cls(analyst_client.client, analyst_client.model_name, **options)
        except AttributeError:
            raise PipelineError("CONFIGURATION_ERROR", "Нужен GeminiLLMClient аналитика.") from None

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> Completion:
        try:
            from google.genai import errors, types
        except ImportError:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Установите дополнение pipeline-generator[team]."
            ) from None
        try:
            result = self.client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=response_schema,
                    candidate_count=1,
                    max_output_tokens=self.max_output_tokens,
                    http_options=types.HttpOptions(
                        timeout=int(self.timeout_seconds * 1000),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                ),
            )
        except httpx.TimeoutException:
            raise PipelineError(
                "LLM_TIMEOUT", "Gemini не ответил вовремя.", retryable=True
            ) from None
        except httpx.TransportError:
            raise PipelineError(
                "LLM_TRANSIENT_ERROR", "Ошибка соединения с Gemini.", retryable=True
            ) from None
        except errors.APIError as error:
            if error.code == 429 or (error.code is not None and error.code >= 500):
                raise PipelineError(
                    "LLM_TRANSIENT_ERROR", "Gemini временно недоступен.", retryable=True
                ) from None
            if error.code in {401, 403}:
                raise PipelineError("LLM_AUTH_ERROR", "Gemini отклонил доступ.") from None
            if error.code == 400 and "location is not supported" in (error.message or "").lower():
                raise PipelineError(
                    "LLM_ACCESS_ERROR", "Gemini недоступен из текущего региона."
                ) from None
            raise PipelineError(
                "CONFIGURATION_ERROR", "Gemini отклонил настройки запроса."
            ) from None
        except ValueError:
            # SDK JSON/Pydantic decoding failures may include raw provider content.
            raise PipelineError("LLM_INVALID_RESPONSE", "Некорректный ответ API Gemini.") from None
        candidates = result.candidates or []
        if not candidates:
            reason = getattr(result.prompt_feedback, "block_reason", None)
            return Completion(
                text="",
                finish_reason=getattr(reason, "value", reason) or "MISSING_CANDIDATE",
                provider="gemini",
                model=self.model,
            )
        if len(candidates) != 1:
            raise PipelineError("LLM_INVALID_RESPONSE", "Ожидался один ответ Gemini.")
        candidate = candidates[0]
        parts = candidate.content.parts if candidate.content else []
        return Completion(
            text="".join(part.text for part in (parts or []) if part.text and not part.thought),
            finish_reason=getattr(candidate.finish_reason, "value", candidate.finish_reason)
            or "UNKNOWN",
            provider="gemini",
            model=result.model_version or self.model,
        )
