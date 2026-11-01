import json
import pytest

from analyst import (
    FormalFeedbackExplainer,
    MockSpecificationBuilder,
    RevisionProposal,
    VerificationExplanation,
)
from formal_check.src.models.output import (
    Counterexample,
    ModelInvalid,
    PropertyHolds,
    PropertyViolated,
    Statistics,
    Step,
    ValidationErrorItem,
)
from pipeline_spec.models import PipelineSpecification


@pytest.fixture
def sample_spec() -> PipelineSpecification:
    builder = MockSpecificationBuilder()
    return builder.build_from_dialogue([])


def test_property_holds_explanation(sample_spec: PipelineSpecification):
    """Verifies clear reporting of successful formal verification."""
    explainer = FormalFeedbackExplainer()
    result = PropertyHolds(
        statistics=Statistics(
            states_generated=42,
            distinct_states=15,
            depth=4,
        )
    )

    explanation = explainer.explain(result, sample_spec)
    assert explanation.status == "PROPERTY_HOLDS"
    assert "✓ Формальная верификация пройдена успешно!" in explanation.title
    assert "42" in explanation.summary
    assert "15" in explanation.summary
    assert explanation.requires_reverification is False
    assert len(explanation.trace_steps) == 0
    assert explanation.proposal is None


def test_property_violated_strictly_uses_tlc_trace(sample_spec: PipelineSpecification):
    """Criteria test: Агент не придумывает контрпример и точно переводит шаги TLC."""
    explainer = FormalFeedbackExplainer()
    result = PropertyViolated(
        property_id="P_NeverWriteWhenInvalid",
        counterexample=Counterexample(
            steps=[
                Step(number=0, transition=None, state={"status": "IDLE", "data_valid": False}),
                Step(number=1, transition="ExecuteFilter", state={"status": "FILTERED", "data_valid": False}),
                Step(number=2, transition="ExecuteWrite", state={"status": "WRITTEN", "data_valid": False}),
            ]
        ),
    )

    explanation = explainer.explain(result, sample_spec)

    assert explanation.status == "PROPERTY_VIOLATED"
    assert "P_NeverWriteWhenInvalid" in explanation.title
    assert explanation.requires_reverification is True

    # Exact step count and transitions without any hallucination
    assert len(explanation.trace_steps) == 3
    assert "Шаг 0 [Начальное состояние (Init)]: status = IDLE, data_valid = False" in explanation.trace_steps[0]
    assert "Шаг 1 [ExecuteFilter]: status = FILTERED, data_valid = False" in explanation.trace_steps[1]
    assert "Шаг 2 [ExecuteWrite]: status = WRITTEN, data_valid = False" in explanation.trace_steps[2]

    # Proposal is formed but NOT agreed by default
    assert explanation.proposal is not None
    assert explanation.proposal.target_property_id == "P_NeverWriteWhenInvalid"
    assert explanation.proposal.is_agreed is False


def test_unresolved_result_not_hidden(sample_spec: PipelineSpecification):
    """Criteria test: Агент не скрывает неустановленный результат (ModelInvalid / Timeout)."""
    explainer = FormalFeedbackExplainer()

    # ModelInvalid case
    invalid_result = ModelInvalid(
        errors=[
            ValidationErrorItem(
                type="INVALID_TYPE",
                location="initial.attempts",
                message="Initial value must be an integer",
            )
        ]
    )
    explanation = explainer.explain(invalid_result, sample_spec)
    assert explanation.status == "MODEL_INVALID"
    assert "initial.attempts" in explanation.summary
    assert explanation.requires_reverification is True

    # Timeout case
    class TimeoutResult:
        status = "TIMEOUT"

    explanation_timeout = explainer.explain(TimeoutResult(), sample_spec)
    assert explanation_timeout.status == "TIMEOUT"
    assert "Неустановленный результат верификации" in explanation_timeout.title


def test_revision_cannot_be_applied_without_user_agreement(sample_spec: PipelineSpecification):
    """Criteria test: При проблеме предлагать изменение, показывать его пользователю

    и применять ТОЛЬКО после согласования.
    """
    explainer = FormalFeedbackExplainer()
    proposal = RevisionProposal(
        issue_summary="Нарушение инварианта",
        proposed_change="Добавить проверку data_valid == True в guard шага Write",
        rationale="Предотвратит запись некорректных данных",
        is_agreed=False,  # User has NOT agreed yet
    )

    # Must raise error if applying without user agreement
    with pytest.raises(ValueError, match="еще не согласовано пользователем"):
        explainer.apply_revision(sample_spec, proposal)


def test_agreed_revision_creates_new_version(sample_spec: PipelineSpecification):
    """Criteria test: Согласованное изменение создаёт новую версию, которую нужно проверить заново."""
    explainer = FormalFeedbackExplainer()
    proposal = RevisionProposal(
        issue_summary="Нарушение инварианта",
        proposed_change="Добавить guard data_valid == True",
        rationale="Предотвратит запись некорректных данных",
        is_agreed=True,  # User confirmed
    )

    initial_version = sample_spec.version  # "1.0.0"
    revised_spec = explainer.apply_revision(sample_spec, proposal)

    assert revised_spec.version != initial_version
    assert revised_spec.version == "1.1.0"
    assert revised_spec.metadata.id == sample_spec.metadata.id

    # The new version is a valid PipelineSpecification
    assert isinstance(revised_spec, PipelineSpecification)
    order = revised_spec.get_topological_order()
    assert len(order) == len(sample_spec.steps)
