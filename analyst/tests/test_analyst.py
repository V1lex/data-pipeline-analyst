import os
import pytest
from analyst import (
    AnalystSession,
    GeminiLLMClient,
    MockLLMClient,
    QuestionCategory,
    Role,
)
from analyst.config import GEMINI_API_KEY


def test_incomplete_query_triggers_clarifications_mock():
    """Criteria test: На неполный запрос «Посчитай выручку по городам»

    агент уточняет источники данных, необходимые поля, формулу и бизнес-правила.
    """
    session = AnalystSession(llm_client=MockLLMClient())
    response = session.send_message("Посчитай выручку по городам")

    assert response.is_complete is False
    assert len(response.clarification_questions) >= 4

    categories = {q.category for q in response.clarification_questions}
    assert QuestionCategory.SOURCES in categories
    assert QuestionCategory.FIELDS in categories
    assert QuestionCategory.FORMULA in categories
    assert QuestionCategory.BUSINESS_RULES in categories

    for q in response.clarification_questions:
        assert len(q.question) > 0
        assert q.suggestion is not None  # Agent provides suggestions for approval


def test_dialogue_history_tracking():
    """Verifies that dialogue history properly records turns and can be reset."""
    session = AnalystSession(llm_client=MockLLMClient())

    session.send_message("Посчитай выручку по городам")
    assert len(session.history) == 2
    assert session.history[0].role == Role.USER
    assert session.history[0].content == "Посчитай выручку по городам"
    assert session.history[1].role == Role.MODEL
    assert len(session.history[1].content) > 0

    session.send_message("Да, подтверждаю. Данные в таблице orders, поля city и amount, выручка = sum(amount), статус = COMPLETED")
    assert len(session.history) == 4
    assert session.last_response is not None
    assert session.last_response.is_complete is True

    session.reset()
    assert len(session.history) == 0
    assert session.last_response is None


@pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY is not configured")
def test_live_gemini_clarification_on_vague_query():
    """Live integration test against Gemini API (gemini-3.5-flash-lite)."""
    client = GeminiLLMClient(api_key=GEMINI_API_KEY)
    session = AnalystSession(llm_client=client)

    response = session.send_message("Посчитай выручку по городам")

    assert response.is_complete is False
    assert len(response.clarification_questions) > 0

    question_texts = " ".join(q.question.lower() for q in response.clarification_questions)
    categories = {q.category for q in response.clarification_questions}

    # Verify key requirements from criteria: sources, fields, formula, business rules
    has_source = QuestionCategory.SOURCES in categories or "источник" in question_texts or "таблиц" in question_texts or "файл" in question_texts
    has_formula_or_fields = (
        QuestionCategory.FORMULA in categories
        or QuestionCategory.FIELDS in categories
        or "выручк" in question_texts
        or "сумм" in question_texts
        or "пол" in question_texts
        or "формул" in question_texts
    )
    has_rules = (
        QuestionCategory.BUSINESS_RULES in categories
        or "правил" in question_texts
        or "фильтр" in question_texts
        or "статус" in question_texts
    )

    assert has_source, f"Expected source questions in {question_texts}"
    assert has_formula_or_fields, f"Expected formula/fields questions in {question_texts}"
    assert has_rules, f"Expected business rule questions in {question_texts}"
