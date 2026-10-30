from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from analyst.config import DEFAULT_MODEL, DEFAULT_TEMPERATURE, GEMINI_API_KEY
from analyst.llm_client import _is_transient_error
from analyst.models import ChatMessage, Role
from analyst.out_of_scope import OutOfScopeError, check_dialogue_scope, check_scope
from pipeline_spec.models import PipelineSpecification

logger = logging.getLogger(__name__)

BUILDER_SYSTEM_INSTRUCTION = """Ты — генератор формальных спецификаций конвейеров данных (Pipeline Specification Builder).
Твоя задача — преобразовать согласованные требования из истории диалога в строгий JSON объект `PipelineSpecification`.

### ТРЕБОВАНИЯ К СПЕЦИФИКАЦИИ:
1. `version`: "1.0.0"
2. `metadata`: id (латиница без пробелов), name, description, author, tags.
3. `sources`: список входных наборов данных (id, name, format, location, schema).
4. `steps`: этапы обработки, образующие ориентированный ациклический граф (DAG).
   - `id`: уникальный идентификатор шага.
   - `inputs`: список ID источников или предшествующих шагов.
   - `depends_on`: явный список зависимостей.
   - `operation`: строго одна из поддерживаемых операций MVP:
     * `read` (source_id)
     * `filter` (condition: логическое выражение)
     * `select` (columns: список ColumnSelection)
     * `with_columns` (columns: список NewColumn с формулами expr)
     * `join` (right_input, join_type: inner/left/right/full/cross, on)
     * `aggregate` (group_by, aggregations: count, sum, avg, min, max, count_distinct)
     * `deduplicate` (subset, keep: first/last)
     * `sort` (order_by: column, direction: asc/desc)
     * `union` (other_inputs, distinct)
     * `write` (sink_id)
5. `sinks`: список выходных целевых датасетов (id, name, from_step, format, location, save_mode, partition_by).
6. `constraints`: правила качества данных (not_null, range, unique, in_set).
7. `formal_model`: конечный автомат для проверки TLA+/TLC:
   - `variables`: enum, boolean, integer переменные состояния пайплайна (например, status, data_valid, attempts).
   - `initial`: предикат начального состояния.
   - `transitions`: переходы между этапами с guard и effects (присваивание 'set').
   - `properties`: свойства invariant, forbidden_state, reachable, deadlock_free.

СТРОГО ЗАПРЕЩЕНО использовать операции за пределами MVP (машинное обучение, потоковые окна и т.д.).
Результат должен быть 100% валидным по схеме `PipelineSpecification`.
"""

MAX_SPEC_JSON_BYTES = 500_000
MAX_SPEC_DEPTH = 30


class FormalCheckConfigError(RuntimeError):
    """Raised when formal_check engine is missing or cannot be imported."""
    pass


class FormalModelValidationError(ValueError):
    """Raised when semantic validation of formal model fails, preserving structured error details."""
    def __init__(self, errors: list[Any], message: str | None = None) -> None:
        self.errors = errors
        msg = message or "; ".join(
            f"[{getattr(e, 'location', 'unknown')}]: {getattr(e, 'message', str(e))}"
            for e in errors
        )
        super().__init__(msg)


def _check_json_depth(obj: Any, current_depth: int = 1, max_depth: int = MAX_SPEC_DEPTH) -> None:
    """Enforces strict bound on JSON structure recursion depth."""
    if current_depth > max_depth:
        raise ValueError(f"JSON structure exceeds maximum allowed recursion depth of {max_depth}.")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_json_depth(v, current_depth + 1, max_depth)
    elif isinstance(obj, list):
        for item in obj:
            _check_json_depth(item, current_depth + 1, max_depth)


def validate_formal_verification_readiness(spec: PipelineSpecification) -> None:
    """Validates that specification is structurally and semantically ready for formal TLC verification (A06)."""
    if spec.formal_model is None:
        raise ValueError(
            "Формальная модель (formal_model) отсутствует. Спецификация не может быть отправлена на верификацию без formal_model."
        )

    if not spec.formal_model.properties:
        raise ValueError("Формальная модель должна содержать хотя бы одно верифицируемое свойство (properties).")

    # Verify model conversion to formal_check canonical Input
    try:
        fc_input = spec.formal_model.to_formal_check_input()
    except Exception as e:
        raise ValueError(f"Ошибка преобразования formal_model в formal_check Input: {e}") from e

    # Ensure formal_check root is in sys.path
    repo_root = Path(__file__).resolve().parent.parent
    formal_check_dir = repo_root / "formal_check"
    if formal_check_dir.exists() and str(formal_check_dir) not in sys.path:
        sys.path.insert(0, str(formal_check_dir))

    try:
        from src.services.validator import validate_input
        from src.models.output import ModelInvalid
    except ImportError as e:
        raise FormalCheckConfigError(
            f"Модуль формальной проверки formal_check недоступен или поврежден: {e}"
        ) from e

    fc_res = validate_input(fc_input.model_dump(by_alias=True, exclude_none=True))
    if isinstance(fc_res, ModelInvalid):
        raise FormalModelValidationError(
            errors=fc_res.errors,
            message="Семантическая валидация formal_model не пройдена: "
            + "; ".join(f"[{getattr(e, 'location', 'unknown')}]: {getattr(e, 'message', str(e))}" for e in fc_res.errors),
        )


class SpecificationBuilder:
    """Builds, validates via Pydantic, and self-corrects PipelineSpecification from requirements."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_retries: int = 3,
        client: genai.Client | None = None,
    ) -> None:
        if client is not None:
            self.client = client
        else:
            key = api_key or GEMINI_API_KEY
            self.client = genai.Client(api_key=key) if key else None
        self.model_name = model_name
        self.temperature = temperature
        self.max_retries = max_retries

    def build_from_dialogue(
        self,
        history: list[ChatMessage],
        require_formal_model: bool = False,
    ) -> PipelineSpecification:
        """Transforms conversation history into validated PipelineSpecification.

        Checks scope on user messages, validates via Pydantic, checks DAG acyclicity,
        and executes self-correction loop on model validation errors.
        """
        # 1. Out-of-scope check: ONLY check user messages across dialogue turns (A10)
        user_messages = [msg.content for msg in history if msg.role == Role.USER]
        scope_res = check_dialogue_scope(user_messages)
        if not scope_res.is_in_scope:
            raise OutOfScopeError(scope_res.reason)

        if not self.client:
            raise RuntimeError("Gemini API client is not initialized (missing API key).")

        # 2. Prepare prompt
        dialogue_text = "\n".join(
            f"{msg.role.upper()}: {msg.content}" for msg in history
        )
        prompt = (
            f"Вот согласованные требования из истории диалога:\n\n{dialogue_text}\n\n"
            f"Сформируй полную, валидную спецификацию PipelineSpecification в формате JSON."
        )

        last_error: str | None = None
        raw_json_text: str = ""

        # 3. Generation and Self-Correction Loop
        for attempt in range(1, self.max_retries + 1):
            if attempt > 1 and last_error:
                logger.info(f"Self-correction attempt {attempt}/{self.max_retries}...")
                current_prompt = (
                    f"{prompt}\n\n"
                    f"ПРЕДЫДУЩАЯ ПОПЫТКА НЕ ПРОШЛА ВАЛИДАЦИЮ:\n{last_error}\n"
                    f"Пожалуйста, исправь ошибки в JSON и верни строго корректный объект."
                )
            else:
                current_prompt = prompt

            # LLM call is isolated from validation errors: SDK config errors are not retried with LLM prompt
            raw_json_text = self._call_llm_json(current_prompt)

            try:
                # Bounded size check
                if len(raw_json_text.encode("utf-8")) > MAX_SPEC_JSON_BYTES:
                    raise ValueError(
                        f"Размер ответа ({len(raw_json_text)} байт) превышает допустимый лимит {MAX_SPEC_JSON_BYTES} байт."
                    )

                # Strip markdown code blocks if wrapped by model
                clean_json_text = raw_json_text.strip()
                if clean_json_text.startswith("```"):
                    lines = clean_json_text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    clean_json_text = "\n".join(lines).strip()

                data = json.loads(clean_json_text)
                _check_json_depth(data)

                # Validate with Pydantic
                spec = PipelineSpecification.model_validate(data)

                # Validate topological order (DAG acyclicity & unified dependencies)
                _ = spec.get_topological_order()

                # Formal model verification check (A06)
                if require_formal_model:
                    validate_formal_verification_readiness(spec)
                elif spec.formal_model is not None:
                    try:
                        validate_formal_verification_readiness(spec)
                    except FormalCheckConfigError:
                        logger.warning("formal_check module not available for non-verifiable draft check.")

                return spec

            except (ValidationError, ValueError, json.JSONDecodeError, FormalModelValidationError) as e:
                last_error = str(e)
                logger.warning(f"Validation failed on attempt {attempt}: {last_error}")
                if attempt == self.max_retries:
                    raise RuntimeError(
                        f"Failed to generate valid PipelineSpecification after {self.max_retries} attempts. "
                        f"Last error: {last_error}"
                    ) from e

        raise RuntimeError("Unexpected builder exit.")

    def build_verifiable_specification(
        self,
        history: list[ChatMessage],
    ) -> PipelineSpecification:
        """Explicit transition to verifiable specification, requiring formal model and semantic validation."""
        return self.build_from_dialogue(history, require_formal_model=True)

    @retry(
        retry=retry_if_exception(_is_transient_error),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_llm_json(self, prompt: str) -> str:
        assert self.client is not None

        # Compatible Gemini Developer API configuration without invalid response_schema (A01)
        config = types.GenerateContentConfig(
            system_instruction=BUILDER_SYSTEM_INSTRUCTION,
            temperature=self.temperature,
            response_mime_type="application/json",
        )
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )

        if not response.candidates:
            raise RuntimeError("Gemini returned no candidates.")

        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        finish_reason_str = str(finish_reason).upper() if finish_reason else ""

        if "MAX_TOKENS" in finish_reason_str:
            raise RuntimeError("Response was truncated due to MAX_TOKENS.")
        if "SAFETY" in finish_reason_str:
            raise RuntimeError("Response was blocked by SAFETY filters.")

        if not response.text:
            raise RuntimeError("Gemini returned empty response text.")

        return response.text

    @staticmethod
    def format_confirmation_summary(spec: PipelineSpecification) -> str:
        """Formats human-readable confirmation summary for the user."""
        order = spec.get_topological_order()
        steps_summary = []
        for idx, sid in enumerate(order, start=1):
            step = next(s for s in spec.steps if s.id == sid)
            steps_summary.append(
                f"  {idx}. [{step.operation.type.upper()}] '{step.name}' (ID: {step.id}) — входы: {step.inputs}"
            )

        sources_summary = [
            f"  - '{s.name}' ({s.id}): формат {s.format.value}, путь '{s.location}'"
            for s in spec.sources
        ]
        sinks_summary = [
            f"  - '{s.name}' ({s.id}): формат {s.format.value}, сохранение в '{s.location}' (режим {s.save_mode.value})"
            for s in spec.sinks
        ]
        constraints_summary = [
            f"  - [{c.level.value.upper()}] {c.name} ({c.type.value}) на шаге '{c.target_step}'"
            for c in spec.constraints
        ] or ["  - (нет ограничений)"]

        formal_summary = "  - (не задана)"
        if spec.formal_model:
            formal_summary = (
                f"  - Переменные: {[v.name for v in spec.formal_model.variables]}\n"
                f"  - Переходы: {[t.name for t in spec.formal_model.transitions]}\n"
                f"  - Свойства: {[p.id for p in spec.formal_model.properties]}"
            )

        return (
            f"======================================================================\n"
            f"ПАЙПЛАЙН: {spec.metadata.name} (ID: {spec.metadata.id}, v{spec.version})\n"
            f"Описание: {spec.metadata.description or '—'}\n\n"
            f"ИСТОЧНИКИ ДАННЫХ ({len(spec.sources)}):\n"
            + "\n".join(sources_summary)
            + f"\n\nЭТАПЫ ОБРАБОТКИ (DAG, {len(spec.steps)} шагов в порядке выполнения):\n"
            + "\n".join(steps_summary)
            + f"\n\nЦЕЛЕВЫЕ ВЫХОДЫ ({len(spec.sinks)}):\n"
            + "\n".join(sinks_summary)
            + f"\n\nПРАВИЛА КАЧЕСТВА ДАННЫХ:\n"
            + "\n".join(constraints_summary)
            + f"\n\nВЕРИФИКАЦИОННАЯ МОДЕЛЬ (TLA+/TLC):\n"
            + formal_summary
            + "\n======================================================================\n"
            f"Пожалуйста, подтвердите спецификацию для запуска проверки и генерации PySpark."
        )


class MockSpecificationBuilder(SpecificationBuilder):
    """Mock specification builder returning deterministic PipelineSpecification for testing."""

    def __init__(
        self,
        canned_spec_dict: dict[str, Any] | None = None,
        canned_raw_json: str | None = None,
    ) -> None:
        super().__init__(api_key="mock_key")
        self.canned_spec_dict = canned_spec_dict
        self.canned_raw_json = canned_raw_json

    def _call_llm_json(self, prompt: str) -> str:
        if self.canned_raw_json is not None:
            return self.canned_raw_json
        if self.canned_spec_dict:
            return json.dumps(self.canned_spec_dict)

        # Default end-to-end sample pipeline specification
        sample = {
            "version": "1.0.0",
            "metadata": {
                "id": "e2e_orders_pipeline",
                "name": "End-to-End Orders Pipeline",
                "description": "Reads orders, filters active, calculates revenue and writes to mart",
            },
            "sources": [
                {
                    "id": "raw_orders",
                    "name": "Raw Orders",
                    "format": "parquet",
                    "location": "s3://lake/orders/",
                }
            ],
            "steps": [
                {
                    "id": "step_read",
                    "name": "Read Orders",
                    "inputs": ["raw_orders"],
                    "operation": {"type": "read", "source_id": "raw_orders"},
                },
                {
                    "id": "step_filter",
                    "name": "Filter Completed",
                    "inputs": ["step_read"],
                    "operation": {
                        "type": "filter",
                        "condition": {
                            "eq": [{"col": "status"}, {"lit": "COMPLETED"}]
                        },
                    },
                },
                {
                    "id": "step_write",
                    "name": "Write Mart",
                    "inputs": ["step_filter"],
                    "operation": {"type": "write", "sink_id": "orders_mart"},
                },
            ],
            "sinks": [
                {
                    "id": "orders_mart",
                    "name": "Orders Analytical Mart",
                    "from_step": "step_write",
                    "format": "parquet",
                    "location": "s3://lake/mart/orders/",
                    "save_mode": "overwrite",
                }
            ],
            "constraints": [
                {
                    "id": "qc_not_null_id",
                    "name": "Order ID Not Null",
                    "target_step": "step_filter",
                    "columns": ["order_id"],
                    "type": "not_null",
                }
            ],
            "formal_model": {
                "variables": [
                    {"name": "status", "type": "enum", "values": ["IDLE", "READ", "WRITTEN"]},
                ],
                "initial": {"status": "IDLE"},
                "transitions": [
                    {
                        "name": "Read",
                        "guard": {"eq": [{"var": "status"}, "IDLE"]},
                        "effects": [{"set": ["status", "READ"]}],
                    },
                    {
                        "name": "Write",
                        "guard": {"eq": [{"var": "status"}, "READ"]},
                        "effects": [{"set": ["status", "WRITTEN"]}],
                    },
                ],
                "properties": [
                    {
                        "id": "P_DeadlockFree",
                        "type": "deadlock_free",
                        "expression": {"eq": [{"var": "status"}, "WRITTEN"]},
                    }
                ],
            },
        }
        return json.dumps(sample)
