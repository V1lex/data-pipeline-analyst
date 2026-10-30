"""Comprehensive regression test matrix covering A01 - A14 review remarks.

Tests explicitly verify:
- SDK_SCHEMA, SDK_INVALID, SDK_TRUNCATED
- CONSENT_NEGATION, CONSENT_NEUTRAL, CONSENT_PARTIAL, CONSENT_STALE
- SUGGESTION_CHANGE, SUGGESTION_TWO_STEPS
- JOIN_INCOMPLETE, FORMULA_INCOMPLETE, WRITE_INCOMPLETE
- DAG_INPUT_CYCLE, DAG_OPERATION_CYCLE, DAG_VALID_BRANCHES
- FORMAL_MISSING, VALIDATOR_MISSING, FORMAL_INVALID
- REVISION_APPLIES, REVISION_NOOP, REVISION_STALE, VERSION_SEPARATION
- FEEDBACK_TIMEOUT, FEEDBACK_TRACE
- SCOPE_NEGATION, PROVIDER_FAILURE
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from google import genai
from google.genai import types
from pydantic import ValidationError

from analyst.completeness import (
    AgreementStatus,
    CompletenessChecker,
    DraftParameter,
    ParameterOrigin,
    SessionState,
)
from analyst.formal_feedback import (
    FormalFeedbackExplainer,
    RevisionProposal,
    VerificationExplanation,
)
from analyst.llm_client import (
    GeminiLLMClient,
    LLMEmptyResponseError,
    LLMTruncatedResponseError,
    MockLLMClient,
)
from analyst.models import (
    AnalystResponse,
    ChatMessage,
    ClarificationQuestion,
    QuestionCategory,
    Role,
)
from analyst.out_of_scope import OutOfScopeError, check_scope
from analyst.session import AnalystSession
from analyst.specification_builder import (
    FormalCheckConfigError,
    FormalModelValidationError,
    MockSpecificationBuilder,
    SpecificationBuilder,
    validate_formal_verification_readiness,
)
from pipeline_spec.models import (
    DataConstraint,
    FileFormat,
    FilterOperation,
    JoinOperation,
    JoinType,
    PipelineMetadata,
    PipelineSpecification,
    PipelineStep,
    ReadOperation,
    SaveMode,
    SelectOperation,
    SinkDataset,
    SourceDataset,
    UnionOperation,
    WriteOperation,
)


def get_sample_valid_spec_dict() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "metadata": {
            "id": "matrix_pipeline",
            "name": "Matrix Pipeline",
            "description": "Test pipeline for matrix verification",
        },
        "sources": [
            {
                "id": "src_orders",
                "name": "Orders",
                "format": "parquet",
                "location": "s3://lake/orders/",
            }
        ],
        "steps": [
            {
                "id": "step_read",
                "name": "Read Orders",
                "inputs": ["src_orders"],
                "operation": {"type": "read", "source_id": "src_orders"},
            },
            {
                "id": "step_write",
                "name": "Write Orders",
                "inputs": ["step_read"],
                "operation": {"type": "write", "sink_id": "sink_orders"},
            },
        ],
        "sinks": [
            {
                "id": "sink_orders",
                "name": "Orders Sink",
                "from_step": "step_write",
                "format": "parquet",
                "location": "s3://lake/sink/",
                "save_mode": "overwrite",
            }
        ],
        "constraints": [],
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


# ==============================================================================
# 1. SDK Tests (SDK_SCHEMA, SDK_INVALID, SDK_TRUNCATED)
# ==============================================================================


def test_SDK_SCHEMA_real_sdk_request_serializes_without_error():
    """SDK_SCHEMA: Real genai.Client with httpx.MockTransport serializes request without additionalProperties error."""
    valid_spec_json = json.dumps(get_sample_valid_spec_dict())

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        # Check that generationConfig requested application/json
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        # Return valid mock response conforming to Gemini API structure
        resp_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": valid_spec_json}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        return httpx.Response(200, json=resp_payload)

    mock_client = genai.Client(
        api_key="fake-test-key",
        http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(mock_handler))
        ),
    )

    builder = SpecificationBuilder(client=mock_client)
    history = [ChatMessage(role=Role.USER, content="Собери пайплайн для заказов")]

    spec = builder.build_from_dialogue(history)
    assert isinstance(spec, PipelineSpecification)
    assert spec.metadata.id == "matrix_pipeline"
    assert spec.get_topological_order() == ["step_read", "step_write"]


def test_SDK_INVALID_malformed_json_handled_safely():
    """SDK_INVALID: Invalid JSON/syntax returns safe controlled error and is retried up to budget."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "INVALID_JSON_NOT_AN_OBJECT{"}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    mock_client = genai.Client(
        api_key="fake-test-key",
        http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(mock_handler))
        ),
    )

    builder = SpecificationBuilder(client=mock_client, max_retries=2)
    history = [ChatMessage(role=Role.USER, content="Создай спецификацию")]

    with pytest.raises(RuntimeError, match="Failed to generate valid PipelineSpecification after 2 attempts"):
        builder.build_from_dialogue(history)


def test_SDK_TRUNCATED_max_tokens_finish_reason_rejected():
    """SDK_TRUNCATED: Truncated response with finish_reason=MAX_TOKENS is rejected immediately."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '{"metadata": {"id": "truncated"'}],
                            "role": "model",
                        },
                        "finishReason": "MAX_TOKENS",
                    }
                ]
            },
        )

    mock_client = genai.Client(
        api_key="fake-test-key",
        http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(mock_handler))
        ),
    )

    builder = SpecificationBuilder(client=mock_client)
    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        builder._call_llm_json("Prompt")


# ==============================================================================
# 2. Consent & Agreement Tests (CONSENT_NEGATION, NEUTRAL, PARTIAL, STALE)
# ==============================================================================


def test_CONSENT_NEGATION_refusal_does_not_approve():
    """CONSENT_NEGATION: 'не согласен', 'не подтверждаю', 'нет' leaves all suggestions unconfirmed/rejected."""
    session = AnalystSession(llm_client=MockLLMClient())
    session.send_message("Посчитай выручку по городам")

    # 5 suggestions generated with PENDING status
    assert len(session.parameters_metadata) == 5
    assert all(p.status == AgreementStatus.PENDING for p in session.parameters_metadata)

    # User explicitly rejects
    response = session.send_message("Нет, не согласен. Не используй предложенные значения.")

    # None of the parameters should be approved!
    assert all(p.status != AgreementStatus.AGREED for p in session.parameters_metadata)
    assert response.is_complete is False


def test_CONSENT_NEUTRAL_words_containing_tokens_do_not_approve():
    """CONSENT_NEUTRAL: Messages like 'данные в CSV', 'пока не знаю', 'дата' do not change agreement status."""
    session = AnalystSession(llm_client=MockLLMClient())
    session.send_message("Посчитай выручку по городам")

    pending_count = len([p for p in session.parameters_metadata if p.status == AgreementStatus.PENDING])
    assert pending_count == 5

    # Neutral messages containing substrings 'да' or 'ок'
    session.send_message("данные в CSV")
    assert all(p.status != AgreementStatus.AGREED for p in session.parameters_metadata)

    session.send_message("пока не знаю точную формулу")
    assert all(p.status != AgreementStatus.AGREED for p in session.parameters_metadata)

    session.send_message("дата заказа в поле order_date")
    assert all(p.status != AgreementStatus.AGREED for p in session.parameters_metadata)


def test_CONSENT_PARTIAL_approving_one_id_leaves_others_pending():
    """CONSENT_PARTIAL: Explicit approval of one ID approves ONLY that item."""
    session = AnalystSession(llm_client=MockLLMClient())
    session.send_message("Посчитай выручку по городам")

    assert len(session.parameters_metadata) == 5
    first_param = session.parameters_metadata[0]

    # Explicitly approve only the first parameter
    success = session.approve_parameter(first_param.param_id)
    assert success is True

    assert first_param.status == AgreementStatus.AGREED
    for other in session.parameters_metadata[1:]:
        assert other.status == AgreementStatus.PENDING

    assert session.get_completeness_report().is_complete is False


def test_CONSENT_STALE_approval_of_old_revision_is_rejected():
    """CONSENT_STALE: Approving an old revision after the value changed is rejected."""
    session = AnalystSession(llm_client=MockLLMClient())
    param = session.add_parameter(
        step_id="step_join",
        field_name="join_type",
        value="left",
        origin=ParameterOrigin.AGENT_SUGGESTION,
        status=AgreementStatus.PENDING,
        revision=1,
    )

    # Later the suggestion is revised to revision 2
    param.value = "inner"
    param.revision = 2
    param.status = AgreementStatus.PENDING

    # Attempting to approve revision 1 must fail
    res = session.approve_parameter("step_join:join_type", revision=1)
    assert res is False
    assert param.status == AgreementStatus.PENDING

    # Approving current revision 2 succeeds
    res_valid = session.approve_parameter("step_join:join_type", revision=2)
    assert res_valid is True
    assert param.status == AgreementStatus.AGREED


# ==============================================================================
# 3. Suggestion Revision Tests (SUGGESTION_CHANGE, SUGGESTION_TWO_STEPS)
# ==============================================================================


def test_SUGGESTION_CHANGE_value_change_resets_status_to_pending():
    """SUGGESTION_CHANGE: If an agreed suggestion changes value, status resets to PENDING with new revision."""
    session = AnalystSession(llm_client=MockLLMClient())

    # Step 1: Initial suggestion
    resp1 = AnalystResponse(
        message="Вопрос",
        is_complete=False,
        clarification_questions=[
            ClarificationQuestion(
                category=QuestionCategory.BUSINESS_RULES,
                question="[step_join] Какой тип соединения?",
                suggestion="left join",
            )
        ],
    )
    session._register_llm_suggestions(resp1)

    # User agrees
    session.approve_parameter("step_join:business_rules")
    param = next(p for p in session.parameters_metadata if p.step_id == "step_join")
    assert param.status == AgreementStatus.AGREED
    assert param.value == "left join"
    assert param.revision == 1

    # Step 2: Next turn agent suggests a different value for the same step and field
    resp2 = AnalystResponse(
        message="Новый вопрос",
        is_complete=False,
        clarification_questions=[
            ClarificationQuestion(
                category=QuestionCategory.BUSINESS_RULES,
                question="[step_join] Изменили правило",
                suggestion="inner join",
            )
        ],
    )
    session._register_llm_suggestions(resp2)

    # Status MUST be reset to PENDING and revision incremented to 2
    assert param.status == AgreementStatus.PENDING
    assert param.value == "inner join"
    assert param.revision == 2


def test_SUGGESTION_TWO_STEPS_same_category_tracked_independently():
    """SUGGESTION_TWO_STEPS: Two join operations have distinct parameters tracked independently."""
    session = AnalystSession(llm_client=MockLLMClient())
    resp = AnalystResponse(
        message="Вопросы по двум join",
        is_complete=False,
        clarification_questions=[
            ClarificationQuestion(
                category=QuestionCategory.BUSINESS_RULES,
                question="[step_join_users] Тип соединения с пользователями?",
                suggestion="left join",
            ),
            ClarificationQuestion(
                category=QuestionCategory.BUSINESS_RULES,
                question="[step_join_payments] Тип соединения с платежами?",
                suggestion="inner join",
            ),
        ],
    )
    session._register_llm_suggestions(resp)

    assert len(session.parameters_metadata) == 2
    p1, p2 = session.parameters_metadata
    assert p1.step_id == "step_join_users"
    assert p2.step_id == "step_join_payments"

    # Approving p1 does NOT approve p2
    session.approve_parameter("step_join_users:business_rules")
    assert p1.status == AgreementStatus.AGREED
    assert p2.status == AgreementStatus.PENDING


# ==============================================================================
# 4. Completeness Tests (JOIN_INCOMPLETE, FORMULA_INCOMPLETE, WRITE_INCOMPLETE)
# ==============================================================================


def test_JOIN_INCOMPLETE_blocks_completion_through_dialogue():
    """JOIN_INCOMPLETE: Join without keys or join type blocks readiness."""
    checker = CompletenessChecker()
    missing = checker.check_operation(
        step_id="step_join",
        step_name="Join Customers",
        operation_type="join",
        params={"right_input": "customers"},
    )
    missing_fields = {m.field_name for m in missing}
    assert "on" in missing_fields
    assert "join_type" in missing_fields

    draft = {
        "sources": [{"id": "s1"}],
        "steps": [{"id": "step_join", "operation": {"type": "join", "right_input": "customers"}}],
        "sinks": [{"id": "k1"}],
    }
    report = checker.check_draft(draft)
    assert report.is_complete is False


def test_FORMULA_INCOMPLETE_requires_exact_expression():
    """FORMULA_INCOMPLETE: Calculation without exact formula raises missing parameter."""
    checker = CompletenessChecker()
    missing = checker.check_operation(
        step_id="step_calc",
        step_name="Calculate",
        operation_type="with_columns",
        params={"columns": [{"name": "discounted_amount", "expr": ""}]},
    )
    assert len(missing) > 0
    assert missing[0].field_name == "columns.expr"


def test_WRITE_INCOMPLETE_no_save_mode_or_sink_not_hallucinated():
    """WRITE_INCOMPLETE: Write operation without target sink or location is incomplete."""
    checker = CompletenessChecker()
    draft = {
        "sources": [{"id": "s1"}],
        "steps": [{"id": "step_write", "operation": {"type": "write"}}],
        "sinks": [],
    }
    report = checker.check_draft(draft)
    assert report.is_complete is False
    missing_fields = {m.field_name for m in report.missing_parameters}
    assert "sinks" in missing_fields or "sink_id" in missing_fields


# ==============================================================================
# 5. DAG Tests (DAG_INPUT_CYCLE, DAG_OPERATION_CYCLE, DAG_VALID_BRANCHES)
# ==============================================================================


def test_DAG_INPUT_CYCLE_hidden_by_explicit_depends_on_detected():
    """DAG_INPUT_CYCLE: Cycle hidden behind an explicit depends_on pointing to source is detected and rejected."""
    raw = get_sample_valid_spec_dict()
    # Step a: inputs=[b], depends_on=[src_orders]
    # Step b: inputs=[a], depends_on=[src_orders]
    raw["steps"] = [
        {
            "id": "step_a",
            "name": "Step A",
            "inputs": ["step_b"],
            "depends_on": ["src_orders"],
            "operation": {"type": "filter", "condition": {"eq": [{"col": "x"}, 1]}},
        },
        {
            "id": "step_b",
            "name": "Step B",
            "inputs": ["step_a"],
            "depends_on": ["src_orders"],
            "operation": {"type": "filter", "condition": {"eq": [{"col": "y"}, 2]}},
        },
    ]
    raw["sinks"][0]["from_step"] = "step_b"

    with pytest.raises(ValidationError, match="Cyclic dependency detected"):
        PipelineSpecification.model_validate(raw)


def test_DAG_OPERATION_CYCLE_via_join_right_input_detected():
    """DAG_OPERATION_CYCLE: Cycle formed through join operation's right_input is detected."""
    raw = get_sample_valid_spec_dict()
    raw["steps"] = [
        {
            "id": "step_1",
            "name": "Step 1",
            "inputs": ["src_orders"],
            "operation": {
                "type": "join",
                "right_input": "step_2",
                "join_type": "inner",
                "on": ["id"],
            },
        },
        {
            "id": "step_2",
            "name": "Step 2",
            "inputs": ["step_1"],
            "operation": {"type": "filter", "condition": {"eq": [{"col": "x"}, 1]}},
        },
    ]
    raw["sinks"][0]["from_step"] = "step_2"

    with pytest.raises(ValidationError, match="Cyclic dependency detected"):
        PipelineSpecification.model_validate(raw)


def test_DAG_VALID_BRANCHES_deterministic_order_preserved():
    """DAG_VALID_BRANCHES: Independent parallel branches execute in deterministic order."""
    raw = get_sample_valid_spec_dict()
    raw["steps"] = [
        {
            "id": "branch_alpha",
            "name": "Branch Alpha",
            "inputs": ["src_orders"],
            "operation": {"type": "filter", "condition": {"eq": [{"col": "x"}, 1]}},
        },
        {
            "id": "branch_beta",
            "name": "Branch Beta",
            "inputs": ["src_orders"],
            "operation": {"type": "filter", "condition": {"eq": [{"col": "y"}, 2]}},
        },
        {
            "id": "final_union",
            "name": "Final Union",
            "inputs": ["branch_alpha"],
            "operation": {"type": "union", "other_inputs": ["branch_beta"], "distinct": False},
        },
    ]
    raw["sinks"][0]["from_step"] = "final_union"

    spec = PipelineSpecification.model_validate(raw)
    order1 = spec.get_topological_order()
    order2 = spec.get_topological_order()

    # Deterministic order
    assert order1 == order2
    assert order1.index("branch_alpha") < order1.index("final_union")
    assert order1.index("branch_beta") < order1.index("final_union")
    assert order1 == ["branch_alpha", "branch_beta", "final_union"]


# ==============================================================================
# 6. Formal Verification Readiness Tests (FORMAL_MISSING, VALIDATOR_MISSING, FORMAL_INVALID)
# ==============================================================================


def test_FORMAL_MISSING_cannot_verify_without_formal_model():
    """FORMAL_MISSING: Missing formal_model on verifiable transition fails."""
    raw = get_sample_valid_spec_dict()
    raw["formal_model"] = None
    spec = PipelineSpecification.model_validate(raw)

    with pytest.raises(ValueError, match="formal_model.*отсутствует"):
        validate_formal_verification_readiness(spec)


def test_VALIDATOR_MISSING_reports_clear_configuration_error():
    """VALIDATOR_MISSING: Missing formal_check engine raises FormalCheckConfigError."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())

    with patch.dict("sys.modules", {"src.services.validator": None}):
        with pytest.raises((FormalCheckConfigError, ImportError)):
            # Force import failure
            with patch("sys.path", []):
                validate_formal_verification_readiness(spec)


def test_FORMAL_INVALID_semantic_errors_retain_field_path():
    """FORMAL_INVALID: Unknown variable in formal model initial state returns structured error with path."""
    raw = get_sample_valid_spec_dict()
    # Unknown variable 'ghost_var' in initial state
    raw["formal_model"]["initial"] = {"ghost_var": "UNKNOWN"}
    spec = PipelineSpecification.model_validate(raw)

    with pytest.raises(FormalModelValidationError) as exc_info:
        validate_formal_verification_readiness(spec)

    err = exc_info.value
    assert len(err.errors) > 0
    err_item = err.errors[0]
    assert "initial.ghost_var" in getattr(err_item, "location", "") or "ghost_var" in getattr(err_item, "message", "")


# ==============================================================================
# 7. Revision & Feedback Tests (REVISION_APPLIES, REVISION_NOOP, REVISION_STALE, VERSION_SEPARATION)
# ==============================================================================


def test_REVISION_APPLIES_tightens_guard_and_modifies_content():
    """REVISION_APPLIES: Agreed revision actually changes the transition guard."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    # Generate proposal for violated property
    steps = [
        {"number": 0, "transition": None, "state": {"status": "READ", "data_valid": False}},
        {"number": 1, "transition": "Write", "state": {"status": "WRITTEN", "data_valid": False}},
    ]
    proposal = explainer._generate_revision_proposal("P_NeverWriteInvalid", steps, spec)
    assert proposal.is_agreed is False
    assert "transitions" in proposal.patch_data

    # User agrees
    proposal.is_agreed = True
    new_spec = explainer.apply_revision(spec, proposal)

    # Verify transition guard actually changed
    old_guard = spec.formal_model.transitions[1].guard
    new_guard = new_spec.formal_model.transitions[1].guard
    assert old_guard != new_guard
    assert "data_valid" in str(new_guard)


def test_REVISION_NOOP_empty_or_identical_patch_rejected():
    """REVISION_NOOP: Patch that does not change content raises error and does not pretend to fix."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    # Empty patch with no proposed change
    proposal = RevisionProposal(
        issue_summary="Проблема",
        proposed_change="",
        rationale="Нет изменений",
        patch_data={},
        is_agreed=True,
    )
    with pytest.raises(ValueError, match="Правка пуста"):
        explainer.apply_revision(spec, proposal)


def test_REVISION_STALE_proposal_for_old_revision_rejected():
    """REVISION_STALE: Proposal created for revision 1 is rejected if spec is now at revision 2."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    proposal = RevisionProposal(
        issue_summary="Проблема",
        proposed_change="Добавить guard",
        rationale="Обоснование",
        base_revision=1,
        patch_data={"steps": spec.model_dump()["steps"]},
        is_agreed=True,
    )

    with pytest.raises(ValueError, match="Предложение устарело"):
        explainer.apply_revision(spec, proposal, current_revision=2)


def test_VERSION_SEPARATION_schema_version_preserved_separately():
    """VERSION_SEPARATION: Specification schema version remains 1.0.0 while requirements revision changes."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    assert spec.version == "1.0.0"

    # Backend manages revision number (e.g. revision 5) without changing format version
    explainer = FormalFeedbackExplainer()
    explanation = explainer.explain(
        {"status": "PROPERTY_HOLDS", "statistics": {"depth": 3, "states_generated": 10, "distinct_states": 5}},
        spec,
        spec_id="matrix_pipeline",
        revision=5,
    )
    assert "ред. 5" in explanation.summary
    assert spec.version == "1.0.0"


# ==============================================================================
# 8. TLC Result Explanation Tests (FEEDBACK_TIMEOUT, FEEDBACK_TRACE)
# ==============================================================================


def test_FEEDBACK_TIMEOUT_indeterminate_status_does_not_claim_correctness():
    """FEEDBACK_TIMEOUT: TIMEOUT status explicitly indicates indeterminate result without claiming correctness."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    explanation = explainer.explain({"status": "TIMEOUT"}, spec)
    assert explanation.status == "TIMEOUT"
    assert "Неустановленный результат верификации" in explanation.title
    assert "не может быть гарантирован" in explanation.summary or "не утверждает корректность" in explanation.summary
    assert explanation.requires_reverification is True
    assert len(explanation.trace_steps) == 0


def test_FEEDBACK_TRACE_strict_counterexample_steps_without_hallucination():
    """FEEDBACK_TRACE: Counterexample trace steps strictly match engine output without fabrication."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    raw_result = {
        "status": "PROPERTY_VIOLATED",
        "property_id": "P_SafeStatus",
        "counterexample": {
            "steps": [
                {"number": 0, "transition": None, "state": {"status": "IDLE"}},
                {"number": 1, "transition": "Read", "state": {"status": "READ"}},
            ]
        },
    }
    explanation = explainer.explain(raw_result, spec)
    assert len(explanation.trace_steps) == 2
    assert "Шаг 0 [Начальное состояние (Init)]: status = IDLE" in explanation.trace_steps[0]
    assert "Шаг 1 [Read]: status = READ" in explanation.trace_steps[1]


# ==============================================================================
# 9. Out of Scope & Provider Failure (SCOPE_NEGATION, PROVIDER_FAILURE)
# ==============================================================================


def test_SCOPE_NEGATION_not_blocked_when_unsupported_term_negated():
    """SCOPE_NEGATION: User message 'streaming не нужен, обрабатываем CSV' is NOT blocked."""
    res1 = check_scope("streaming не нужен, обрабатываем обычный CSV батч")
    assert res1.is_in_scope is True

    res2 = check_scope("без нейросетей, обычный расчет агрегатов")
    assert res2.is_in_scope is True

    res3 = check_scope("откажись от kafka stream, делаем batch")
    assert res3.is_in_scope is True

    # Real out of scope query is still rejected
    res_blocked = check_scope("Построй мне streaming обработку с sliding window")
    assert res_blocked.is_in_scope is False
    assert res_blocked.unsupported_feature == "streaming_windowing"


def test_PROVIDER_FAILURE_no_silent_fallback_to_mock():
    """PROVIDER_FAILURE: Missing API key or configuration failure raises error instead of silently using Mock."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "", "USE_MOCK_CLIENT": "false"}):
        with pytest.raises(ValueError, match="GEMINI_API_KEY is not provided"):
            # Should NOT silently return MockLLMClient
            AnalystSession()


# ==============================================================================
# 10. Deep Edge Cases & Adversarial Invariant Tests
# ==============================================================================


def test_CONSENT_NEGATION_compound_all_refusal_does_not_approve():
    """Verifies that phrases like 'не согласен со всеми' or 'не подтверждаю все' never approve."""
    session = AnalystSession(llm_client=MockLLMClient())
    resp = AnalystResponse(
        message="Вопросы",
        is_complete=False,
        clarification_questions=[
            ClarificationQuestion(category=QuestionCategory.SOURCES, question="Источник?", suggestion="s3://lake"),
            ClarificationQuestion(category=QuestionCategory.FIELDS, question="Поля?", suggestion="id, amount"),
        ],
    )
    session._register_llm_suggestions(resp)
    assert len(session.parameters_metadata) == 2
    assert all(p.status == AgreementStatus.PENDING for p in session.parameters_metadata)

    # 1. "не согласен со всеми"
    session._check_user_agreement("Я не согласен со всеми предложенными параметрами!")
    assert all(p.status == AgreementStatus.REJECTED for p in session.parameters_metadata)

    # Reset to PENDING
    for p in session.parameters_metadata:
        p.status = AgreementStatus.PENDING

    # 2. "не подтверждаю все"
    session._check_user_agreement("не подтверждаю все")
    assert all(p.status == AgreementStatus.REJECTED for p in session.parameters_metadata)


def test_CONSENT_TARGETED_REJECTION_by_step_and_param():
    """Targeted rejections via 'не согласен с ...' or 'не подтверждаю ...' reject only target."""
    session = AnalystSession(llm_client=MockLLMClient())
    resp = AnalystResponse(
        message="Вопросы",
        is_complete=False,
        clarification_questions=[
            ClarificationQuestion(category=QuestionCategory.BUSINESS_RULES, question="[step_join] Тип?", suggestion="left join"),
            ClarificationQuestion(category=QuestionCategory.FORMULA, question="[step_calc] Формула?", suggestion="a + b"),
        ],
    )
    session._register_llm_suggestions(resp)
    p_join = next(p for p in session.parameters_metadata if p.step_id == "step_join")
    p_calc = next(p for p in session.parameters_metadata if p.step_id == "step_calc")

    # Reject step_join
    session._check_user_agreement("не согласен с step_join:business_rules")
    assert p_join.status == AgreementStatus.REJECTED
    assert p_calc.status == AgreementStatus.PENDING

    # Approve step_calc by step_id
    session.approve_parameter("step_calc")
    assert p_calc.status == AgreementStatus.AGREED


def test_FEEDBACK_JSON_STRING_INPUT_explained_correctly():
    """TLC output passed as JSON string is properly parsed and explained."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    explainer = FormalFeedbackExplainer()

    json_str = json.dumps({
        "status": "PROPERTY_HOLDS",
        "statistics": {"depth": 4, "states_generated": 12, "distinct_states": 7},
    })
    explanation = explainer.explain(json_str, spec, spec_id="test_pipeline", revision=2)
    assert explanation.status == "PROPERTY_HOLDS"
    assert "ред. 2" in explanation.summary
    assert "Исследовано состояний: 12" in explanation.summary


def test_WRITE_INCOMPLETE_missing_save_mode_reported():
    """Draft with sink but missing save_mode is incomplete and reports missing save_mode."""
    checker = CompletenessChecker()
    draft = {
        "sources": [{"id": "s1"}],
        "steps": [{"id": "step_write", "operation": {"type": "write", "sink_id": "mart_sink"}}],
        "sinks": [{"id": "mart_sink", "location": "s3://lake/mart"}],
    }
    report = checker.check_draft(draft)
    assert report.is_complete is False
    missing_fields = {m.field_name for m in report.missing_parameters}
    assert "save_mode" in missing_fields


def test_SCOPE_DECISION_CHANGE_to_batch_in_subsequent_turn():
    """A user who asks for streaming in Turn 1 and changes mind to batch in Turn 2 is accepted."""
    builder = MockSpecificationBuilder()
    history = [
        ChatMessage(role=Role.USER, content="Сделай streaming обработку заказов"),
        ChatMessage(role=Role.MODEL, content="Streaming не поддерживается."),
        ChatMessage(role=Role.USER, content="Хорошо, streaming не нужен, посчитай выручку по городам из CSV батчем"),
    ]
    spec = builder.build_from_dialogue(history)
    assert spec is not None
    assert spec.metadata.id is not None


def test_SPEC_BUILDER_MARKDOWN_CODE_FENCES_STRIPPED():
    """Builder cleanly strips markdown fences around JSON output."""
    raw_spec = get_sample_valid_spec_dict()
    wrapped_json = f"```json\n{json.dumps(raw_spec, indent=2)}\n```"

    builder = MockSpecificationBuilder(canned_raw_json=wrapped_json)
    history = [ChatMessage(role=Role.USER, content="Создай спецификацию")]
    spec = builder.build_from_dialogue(history)
    assert spec.metadata.id == "matrix_pipeline"


def test_REVISION_PRESERVE_SCHEMA_VERSION():
    """apply_revision with bump_schema_version=False preserves specification format version."""
    spec = PipelineSpecification.model_validate(get_sample_valid_spec_dict())
    assert spec.version == "1.0.0"

    explainer = FormalFeedbackExplainer()
    steps = [
        {"number": 0, "transition": None, "state": {"status": "READ", "data_valid": False}},
        {"number": 1, "transition": "Write", "state": {"status": "WRITTEN", "data_valid": False}},
    ]
    proposal = explainer._generate_revision_proposal("P_NeverWriteInvalid", steps, spec)
    proposal.is_agreed = True

    revised = explainer.apply_revision(spec, proposal, bump_schema_version=False, current_revision=1)
    assert revised.version == "1.0.0"
    assert "data_valid" in str(revised.formal_model.transitions)

