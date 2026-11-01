from copy import deepcopy

import pytest
from agent.analyst.completeness import AgreementStatus, DraftParameter, ParameterOrigin
from agent.analyst.models import AnalystResponse, ClarificationQuestion
from agent.analyst.session import AnalystSession
from pipeline_spec.models import PipelineSpecification


class Replies:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def generate_response(self, **kwargs):
        reply = next(self.responses)
        if isinstance(reply, Exception):
            raise reply
        return reply


def draft():
    return {
        "sources": [{"id": "orders", "name": "Orders", "format": "csv", "location": "orders.csv"}],
        "steps": [
            {
                "id": "read",
                "name": "Read",
                "inputs": ["orders"],
                "operation": {"type": "read", "source_id": "orders"},
            },
            {
                "id": "first",
                "name": "First filter",
                "inputs": ["read"],
                "operation": {
                    "type": "filter",
                    "condition": {"gt": [{"col": "amount"}, {"lit": 0}]},
                },
            },
            {
                "id": "second",
                "name": "Second filter",
                "inputs": ["first"],
                "operation": {
                    "type": "filter",
                    "condition": {"lt": [{"col": "amount"}, {"lit": 100}]},
                },
            },
            {
                "id": "write",
                "name": "Write",
                "inputs": ["second"],
                "operation": {"type": "write", "sink_id": "result"},
            },
        ],
        "sinks": [
            {
                "id": "result",
                "name": "Result",
                "from_step": "write",
                "location": "result.parquet",
                "format": "parquet",
                "save_mode": "error_if_exists",
            }
        ],
    }


def test_structured_explicit_answers_preserve_repeated_steps_and_changed_values():
    initial = draft()
    PipelineSpecification.model_validate({"metadata": {"id": "test", "name": "Test"}, **initial})
    revised = deepcopy(initial)
    revised["steps"][1]["operation"]["condition"] = {"gt": [{"col": "amount"}, {"lit": 10}]}
    session = AnalystSession(
        llm_client=Replies(
            AnalystResponse(message="Собрано", is_complete=True, draft=initial),
            AnalystResponse(message="Изменено", is_complete=True, draft=revised),
        )
    )
    session.send_message("orders.csv; два фильтра, 0 < amount < 100; результат result.parquet")
    assert session.current_draft == initial
    session.send_message("Первый фильтр теперь amount > 10")
    assert session.current_draft == revised
    assert session.get_completeness_report().is_complete


def test_provider_failure_does_not_mutate_session_or_approve_pending():
    session = AnalystSession(llm_client=Replies(RuntimeError("provider unavailable")))
    session.add_parameter(
        "first",
        "condition",
        {"gt": [{"col": "amount"}, {"lit": 0}]},
        ParameterOrigin.AGENT_SUGGESTION,
    )
    before = session.snapshot()
    with pytest.raises(RuntimeError):
        session.send_message("Да")
    assert session.snapshot() == before


def test_restore_keeps_draft_and_field_suggestion_identity():
    reply = AnalystResponse(
        message="Уточните",
        is_complete=False,
        draft=draft(),
        clarification_questions=[
            ClarificationQuestion(
                category="business_rules",
                question="Тип?",
                suggestion="left",
                step_id="join",
                field_name="join_type",
                value="left",
            ),
            ClarificationQuestion(
                category="business_rules",
                question="Вход?",
                suggestion="orders",
                step_id="join",
                field_name="right_input",
                value="orders",
            ),
        ],
    )
    session = AnalystSession(llm_client=Replies(reply))
    session.send_message("Соедини заказы")
    restored = AnalystSession.restore(
        session.snapshot(), llm_client=Replies(AnalystResponse(message="Ок", is_complete=False))
    )
    assert restored.snapshot() == session.snapshot()
    assert {p.param_id for p in restored.parameters_metadata} >= {
        "join:join_type",
        "join:right_input",
    }


def test_restored_turn_receives_structured_draft_and_approval_context():
    class InspectContext:
        def generate_response(self, *, messages, system_instruction):
            assert '"id": "first"' in system_instruction
            assert '"param_id": "first:condition"' in system_instruction
            assert '"status": "pending"' in system_instruction
            return AnalystResponse(message="Данные сохранены", is_complete=False, draft=draft())

    session = AnalystSession(llm_client=Replies())
    session.set_draft(draft())
    session.add_parameter(
        "first",
        "condition",
        {"gt": [{"col": "amount"}, {"lit": 0}]},
        ParameterOrigin.AGENT_SUGGESTION,
    )
    restored = AnalystSession.restore(session.snapshot(), llm_client=InspectContext())
    restored.send_message("Уточни первый фильтр")


@pytest.mark.parametrize(
    "change", ["source_location", "empty_steps", "unknown_op", "undefined_input", "cycle"]
)
def test_invalid_draft_never_becomes_ready(change):
    data = draft()
    if change == "source_location":
        del data["sources"][0]["location"]
    elif change == "empty_steps":
        data["steps"] = []
    elif change == "unknown_op":
        data["steps"][1]["operation"] = {"type": "machine_learning"}
    elif change == "undefined_input":
        data["steps"][1]["inputs"] = ["missing"]
    else:
        data["steps"][1]["inputs"] = ["second"]
    session = AnalystSession(
        llm_client=Replies(AnalystResponse(message="Готово", is_complete=True, draft=data))
    )
    response = session.send_message("Построй пайплайн")
    assert not response.is_complete
    report = session.get_completeness_report()
    assert not report.is_complete
    assert report.missing_parameters
    assert not report.pending_suggestions


def test_explicit_parameter_update_replaces_pending_value_and_prunes_deleted_step():
    revised = draft()
    revised["steps"] = [s for s in revised["steps"] if s["id"] != "second"]
    revised["steps"][-1]["inputs"] = ["first"]
    condition = {"gt": [{"col": "amount"}, {"lit": 10}]}
    response = AnalystResponse(
        message="Изменено",
        is_complete=True,
        draft=revised,
        parameter_updates=[
            DraftParameter(
                step_id="first",
                field_name="condition",
                value=condition,
                origin="user_explicit",
                revision=2,
            )
        ],
    )
    session = AnalystSession(llm_client=Replies(response))
    session.set_draft(draft())
    session.add_parameter(
        "first",
        "condition",
        {"gt": [{"col": "amount"}, {"lit": 0}]},
        ParameterOrigin.AGENT_SUGGESTION,
    )
    session.add_parameter("second", "condition", {}, ParameterOrigin.AGENT_SUGGESTION)
    result = session.send_message("Первый фильтр amount > 10, второй удали")
    assert result.is_complete
    assert result.draft["steps"][1]["operation"]["condition"] == condition
    assert len(session.parameters_metadata) == 1
    param = session.parameters_metadata[0]
    assert param.param_id == "first:condition"
    assert param.revision == 2 and param.status == AgreementStatus.AGREED
    assert session.current_draft["steps"][1]["operation"]["condition"] == condition


def test_agent_parameter_update_cannot_self_approve_and_applies_only_after_consent():
    condition = {"gt": [{"col": "amount"}, {"lit": 10}]}
    session = AnalystSession(
        llm_client=Replies(
            AnalystResponse(
                message="Предлагаю",
                is_complete=True,
                draft=draft(),
                parameter_updates=[
                    DraftParameter(
                        step_id="first",
                        field_name="condition",
                        value=condition,
                        origin="agent_suggestion",
                        status="agreed",
                    )
                ],
            ),
            AnalystResponse(message="Принято", is_complete=True, draft=draft()),
        )
    )
    assert not session.send_message("Предложи порог").is_complete
    assert session.parameters_metadata[0].status == AgreementStatus.PENDING
    assert session.current_draft["steps"][1]["operation"]["condition"] != condition
    assert session.send_message("approve first:condition rev 1").is_complete
    assert session.current_draft["steps"][1]["operation"]["condition"] == condition


def test_text_only_deterministic_questions_are_not_concrete_parameters():
    data = draft()
    del data["steps"][1]["operation"]["condition"]
    session = AnalystSession(
        llm_client=Replies(AnalystResponse(message="Уточните", is_complete=False, draft=data))
    )
    response = session.send_message("Добавь фильтр")
    assert any(q.field_name == "condition" for q in response.clarification_questions)
    assert not session.parameters_metadata


@pytest.mark.parametrize("revision,param_id", [(1, "first:condition"), (2, "other:condition")])
def test_invalid_parameter_identity_or_stale_revision_rolls_back(revision, param_id):
    session = AnalystSession(
        llm_client=Replies(
            AnalystResponse(
                message="Изменено",
                is_complete=True,
                draft=draft(),
                parameter_updates=[
                    DraftParameter(
                        step_id="first",
                        field_name="condition",
                        value={"gt": [{"col": "amount"}, {"lit": 20}]},
                        origin="user_explicit",
                        revision=revision,
                        param_id=param_id,
                    )
                ],
            )
        )
    )
    session.set_draft(draft())
    session.add_parameter(
        "first",
        "condition",
        {"gt": [{"col": "amount"}, {"lit": 10}]},
        ParameterOrigin.AGENT_SUGGESTION,
        revision=2,
    )
    before = session.snapshot()
    with pytest.raises(ValueError, match="revision|target"):
        session.send_message("Измени условие")
    assert session.snapshot() == before


def test_legacy_category_suggestion_does_not_invent_source_or_sink_paths():
    session = AnalystSession(
        llm_client=Replies(
            AnalystResponse(
                message="Ок",
                is_complete=True,
                identified_sources=["orders"],
                identified_operations=["read", "write"],
            )
        )
    )
    session.add_parameter(
        "step_sinks",
        "sinks",
        "предложить место сохранения",
        ParameterOrigin.AGENT_SUGGESTION,
        status=AgreementStatus.AGREED,
    )
    response = session.send_message("Продолжай")
    assert not response.is_complete
    assert "location" not in session.current_draft["sources"][0]
    assert session.current_draft["sinks"] == []


def test_new_question_suggestion_does_not_inherit_explicit_value_approval():
    condition = {"gt": [{"col": "amount"}, {"lit": 10}]}
    session = AnalystSession(
        llm_client=Replies(
            AnalystResponse(
                message="Предлагаю другой порог",
                is_complete=True,
                draft=draft(),
                clarification_questions=[
                    ClarificationQuestion(
                        category="business_rules",
                        question="Изменить порог?",
                        suggestion="amount > 10",
                        step_id="first",
                        field_name="condition",
                        value=condition,
                    )
                ],
            )
        )
    )
    session.set_draft(draft())
    session.add_parameter(
        "first",
        "condition",
        {"gt": [{"col": "amount"}, {"lit": 0}]},
        ParameterOrigin.USER_EXPLICIT,
        status=AgreementStatus.AGREED,
    )
    assert not session.send_message("Что можно улучшить?").is_complete
    param = session.parameters_metadata[0]
    assert param.origin == ParameterOrigin.AGENT_SUGGESTION
    assert param.status == AgreementStatus.PENDING and param.revision == 2
    assert session.get_completeness_report().pending_suggestions
