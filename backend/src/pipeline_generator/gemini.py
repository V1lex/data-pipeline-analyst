"""Gemini REST adapter. No model/provider fallback or server-side code execution."""

import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from .errors import PipelineError
from .models import Completion


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_output_tokens: int = 8192,
        http: httpx.Client | None = None,
    ):
        if (
            not api_key.strip()
            or not re.fullmatch(r"[a-zA-Z0-9._-]+", model)
            or not 0 < timeout_seconds <= 300
            or not 256 <= max_output_tokens <= 65536
        ):
            raise PipelineError(
                "CONFIGURATION_ERROR", "Проверьте ключ Gemini, модель, тайм-аут и лимит вывода."
            )
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client()

    @classmethod
    def from_environment(cls, *, env_file: str | Path | None = None):
        settings = dict(dotenv_values(env_file, interpolate=False)) if env_file is not None else {}
        settings.update(os.environ)
        try:
            return cls(
                api_key=settings.get("GEMINI_API_KEY") or "",
                model=settings.get("GEMINI_MODEL") or "",
                timeout_seconds=float(settings.get("LLM_TIMEOUT_SECONDS") or "60"),
                max_output_tokens=int(settings.get("LLM_MAX_OUTPUT_TOKENS") or "8192"),
            )
        except ValueError:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Лимиты Gemini должны быть числами."
            ) from None

    def close(self):
        if self._owns_http:
            self._http.close()

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> Completion:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": response_schema,
                "candidateCount": 1,
                "maxOutputTokens": self.max_output_tokens,
            },
        }
        try:
            response = self._http.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json=payload,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise PipelineError(
                "LLM_TIMEOUT", "Gemini не ответил за отведённое время.", retryable=True
            ) from None
        except httpx.TransportError:
            raise PipelineError(
                "LLM_TRANSIENT_ERROR", "Временная ошибка соединения с Gemini.", retryable=True
            ) from None
        if response.status_code in {401, 403}:
            raise PipelineError(
                "LLM_AUTH_ERROR", "Gemini отклонил доступ: проверьте ключ и доступность API."
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise PipelineError(
                "LLM_TRANSIENT_ERROR",
                "Gemini временно недоступен или исчерпана квота.",
                retryable=True,
            )
        if response.status_code != 200:
            if response.status_code == 400:
                try:
                    diagnostic = response.json().get("error", {})
                    region_denied = (
                        diagnostic.get("status") == "FAILED_PRECONDITION"
                        and "location is not supported" in diagnostic.get("message", "").lower()
                    )
                except (ValueError, TypeError, AttributeError):
                    region_denied = False
                if region_denied:
                    raise PipelineError(
                        "LLM_ACCESS_ERROR",
                        "Gemini недоступен из текущего региона выполнения запроса.",
                    )
            raise PipelineError(
                "CONFIGURATION_ERROR", "Gemini отклонил запрос: проверьте модель и настройки."
            )
        try:
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                reason = data.get("promptFeedback", {}).get("blockReason", "MISSING_CANDIDATE")
                return Completion(
                    text="", finish_reason=str(reason), provider="gemini", model=self.model
                )
            if len(candidates) != 1:
                raise ValueError("Expected one candidate")
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(
                part["text"] for part in parts if "text" in part and not part.get("thought", False)
            )
            return Completion(
                text=text,
                finish_reason=candidate.get("finishReason", "UNKNOWN"),
                provider="gemini",
                model=data.get("modelVersion", self.model),
            )
        except (ValueError, TypeError, AttributeError, KeyError):
            raise PipelineError("LLM_INVALID_RESPONSE", "Некорректный ответ API Gemini.") from None
