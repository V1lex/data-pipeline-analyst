from typing import Any

import pytest
from analyst.completeness import (
    AgreementStatus,
    CompletenessChecker,
    DraftParameter,
    ParameterOrigin,
)
from analyst.llm_client import MockLLMClient
from analyst.models import AnalystResponse
from analyst.session import AnalystSession
from pipeline_spec.models.enums import OperationType


def test_join_operation_mandatory_parameters():
    """Verify join requires: right_input, on (key), join_type (unmatched rows rule)."""
    checker = CompletenessChecker()

    # Empty join params
    missing = checker.check_operation(
        step_id="join_step",
        step_name="Join Orders",
        operation_type=OperationType.JOIN,
        params={},
    )
    field_names = {m.field_name for m in missing}
    assert "right_input" in field_names
    assert "on" in field_names
    assert "join_type" in field_names

    # Check join with only right_input provided, but missing key and type
    missing_partial = checker.check_operation(
        step_id="join_step",
        step_name="Join Orders",
        operation_type=OperationType.JOIN,
        params={"right_input": "customers_table"},
    )
    partial_fields = {m.field_name for m in missing_partial}
    assert "on" in partial_fields
    assert "join_type" in partial_fields
    assert "right_input" not in partial_fields


def test_with_columns_operation_formula_required():
    """Verify with_columns requires formula / expression."""
    checker = CompletenessChecker()

    missing = checker.check_operation(
        step_id="calc_step",
        step_name="Calculate Revenue",
        operation_type=OperationType.WITH_COLUMNS,
        params={"columns": []},
    )
    field_names = {m.field_name for m in missing}
    assert "columns" in field_names


def test_aggregate_operation_grouping_and_aggregations_required():
    """Verify aggregate requires group_by and aggregations."""
    checker = CompletenessChecker()

    missing = checker.check_operation(
        step_id="agg_step",
        step_name="Aggregate Sales",
        operation_type=OperationType.AGGREGATE,
        params={"group_by": []},
    )
    field_names = {m.field_name for m in missing}
    assert "group_by" in field_names
    assert "aggregations" in field_names


def test_agent_does_not_complete_when_parameters_missing():
    """Criteria test: Агент не завершает подготовку спецификации при отсутствии обязательных параметров."""
    session = AnalystSession(llm_client=MockLLMClient())

    # Draft with missing join key and missing aggregate formula
    draft = {
        "sources": [{"id": "orders", "format": "parquet", "location": "s3://orders"}],
        "steps": [
            {
                "id": "step_join",
                "name": "Join Customers",
                "operation": {"type": "join", "right_input": "customers"},  # missing on, join_type
            },
            {
                "id": "step_agg",
                "name": "Aggregate Revenue",
                "operation": {"type": "aggregate"},  # missing group_by, aggregations
            },
        ],
        "sinks": [{"id": "output", "format": "parquet", "location": "s3://mart"}],
    }
    session.set_draft(draft)

    # Even if LLM would want to finish, completeness checker forces is_complete = False
    response = session.send_message("Я хочу закончить и сделать спецификацию")
    assert response.is_complete is False

    report = session.get_completeness_report()
    assert report.is_complete is False
    assert len(report.missing_parameters) > 0

    # Programmatic check discovered missing join keys and aggregate fields
    missing_fields = {m.field_name for m in report.missing_parameters}
    assert "on" in missing_fields
    assert "join_type" in missing_fields
    assert "group_by" in missing_fields
    assert "aggregations" in missing_fields


def test_agent_suggestions_distinguished_and_require_agreement():
    """Criteria test: Предложения агента отличаются от требований пользователя

    и показываются для согласования.
    """
    session = AnalystSession(llm_client=MockLLMClient())

    # 1. User specifies a requirement explicitly
    session.add_parameter(
        step_id="step_join",
        field_name="on",
        value=["customer_id"],
        origin=ParameterOrigin.USER_EXPLICIT,
        status=AgreementStatus.AGREED,
        description="Ключ соединения задан пользователем",
    )

    # 2. Agent suggests a default join_type
    session.add_parameter(
        step_id="step_join",
        field_name="join_type",
        value="left",
        origin=ParameterOrigin.AGENT_SUGGESTION,
        status=AgreementStatus.PENDING,
        description="Предложение агента: использовать left join",
    )

    # Check distinction in report
    report = session.get_completeness_report()
    assert len(report.confirmed_parameters) == 1
    assert report.confirmed_parameters[0].origin == ParameterOrigin.USER_EXPLICIT
    assert report.confirmed_parameters[0].field_name == "on"

    assert len(report.pending_suggestions) == 1
    assert report.pending_suggestions[0].origin == ParameterOrigin.AGENT_SUGGESTION
    assert report.pending_suggestions[0].field_name == "join_type"

    # Pending suggestion prevents completion
    assert report.is_complete is False

    # 3. User agrees to the suggestion
    session.send_message(
        "Да, я согласен с предложенным left join для соединения orders и customers по ключу customer_id"
    )
    report_after = session.get_completeness_report()
    assert len(report_after.pending_suggestions) == 0
    assert len(report_after.confirmed_parameters) == 2
