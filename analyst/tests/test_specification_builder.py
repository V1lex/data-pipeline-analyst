import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from analyst import (
    ChatMessage,
    MockSpecificationBuilder,
    OutOfScopeError,
    Role,
    SpecificationBuilder,
    check_scope,
)
from pipeline_spec.models import PipelineSpecification


def test_out_of_scope_rejection():
    """Criteria test: Неподдерживаемые операции не выдаются за реализованные."""
    builder = MockSpecificationBuilder()

    # Machine Learning request
    ml_history = [
        ChatMessage(
            role=Role.USER,
            content="Сделай пайплайн: прочитай данные и обучи модель градиентного бустинга для классификации",
        )
    ]
    with pytest.raises(OutOfScopeError, match="machine_learning"):
        builder.build_from_dialogue(ml_history)

    # Streaming windowing request
    streaming_history = [
        ChatMessage(
            role=Role.USER,
            content="Нужна потоковая обработка из Kafka с sliding window 5 минут",
        )
    ]
    with pytest.raises(OutOfScopeError, match="streaming_windowing"):
        builder.build_from_dialogue(streaming_history)


def test_end_to_end_specification_generation_and_validation():
    """Criteria test: Для сквозного примера получается JSON, который принимают Validator и генератор."""
    builder = MockSpecificationBuilder()
    history = [
        ChatMessage(
            role=Role.USER,
            content="Посчитай выручку по заказам: прочитать raw_orders, отфильтровать статус COMPLETED и сохранить в mart",
        ),
        ChatMessage(
            role=Role.MODEL,
            content="Уточните формат и путь.",
        ),
        ChatMessage(
            role=Role.USER,
            content="Формат Parquet, путь s3://lake/orders/, сохранить в s3://lake/mart/orders/",
        ),
    ]

    spec = builder.build_from_dialogue(history)

    assert isinstance(spec, PipelineSpecification)
    assert spec.metadata.id == "e2e_orders_pipeline"
    assert len(spec.sources) == 1
    assert len(spec.steps) == 3
    assert len(spec.sinks) == 1

    # Check topological order is strictly valid
    order = spec.get_topological_order()
    assert order == ["step_read", "step_filter", "step_write"]

    # Check formal_model is valid and convertible for formal_check
    assert spec.formal_model is not None
    fc_input = spec.formal_model.to_formal_check_input()
    assert len(fc_input.variables) == 1
    assert len(fc_input.transitions) == 2
    assert len(fc_input.properties) == 1


def test_user_confirmation_summary_formatting():
    """Verifies that human-readable confirmation summary contains all sections."""
    builder = MockSpecificationBuilder()
    spec = builder.build_from_dialogue([ChatMessage(role=Role.USER, content="Создай пайплайн")])

    summary = builder.format_confirmation_summary(spec)
    assert "ПАЙПЛАЙН: End-to-End Orders Pipeline" in summary
    assert "ИСТОЧНИКИ ДАННЫХ" in summary
    assert "ЭТАПЫ ОБРАБОТКИ (DAG" in summary
    assert "ЦЕЛЕВЫЕ ВЫХОДЫ" in summary
    assert "ПРАВИЛА КАЧЕСТВА ДАННЫХ" in summary
    assert "ВЕРИФИКАЦИОННАЯ МОДЕЛЬ (TLA+/TLC)" in summary
    assert "Пожалуйста, подтвердите спецификацию" in summary


def test_self_correction_loop_on_pydantic_error():
    """Verifies that self-correction retries when the first generation attempt is invalid."""
    builder = SpecificationBuilder(api_key="mock_key")

    # Invalid JSON on first attempt (cycle: step1 depends on step2, step2 depends on step1)
    invalid_spec = {
        "version": "1.0.0",
        "metadata": {"id": "invalid", "name": "Invalid"},
        "sources": [{"id": "s1", "name": "Source 1", "format": "csv", "location": "path"}],
        "steps": [
            {
                "id": "step1",
                "name": "Step 1",
                "depends_on": ["step2"],
                "operation": {"type": "read", "source_id": "s1"},
            },
            {
                "id": "step2",
                "name": "Step 2",
                "depends_on": ["step1"],
                "operation": {"type": "read", "source_id": "s1"},
            },
        ],
        "sinks": [],
    }

    # Valid spec on second attempt
    valid_spec = {
        "version": "1.0.0",
        "metadata": {"id": "valid", "name": "Valid"},
        "sources": [{"id": "s1", "name": "Source 1", "format": "csv", "location": "path"}],
        "steps": [
            {
                "id": "step1",
                "name": "Step 1",
                "operation": {"type": "read", "source_id": "s1"},
            }
        ],
        "sinks": [],
    }

    builder._call_llm_json = MagicMock(
        side_effect=[json.dumps(invalid_spec), json.dumps(valid_spec)]
    )

    spec = builder.build_from_dialogue([ChatMessage(role=Role.USER, content="Создай пайплайн")])
    assert spec.metadata.id == "valid"
    assert builder._call_llm_json.call_count == 2
