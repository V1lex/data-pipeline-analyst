#!/usr/bin/env python3
"""Interactive CLI dialogue with Data Pipeline Analyst."""

import sys
from analyst.session import AnalystSession


def main() -> None:
    print("=" * 70)
    print("ИИ-Аналитик конвейеров данных (Data Pipeline Analyst) — Режим диалога")
    print("Введите ваш запрос по пайплайну данных (или 'exit' для выхода, 'reset' для сброса):")
    print("=" * 70)

    session = AnalystSession()

    while True:
        try:
            user_input = input("\nВы > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("Сессия завершена.")
            break
        if user_input.lower() == "reset":
            session.reset()
            print("История диалога очищена.")
            continue

        print("\n[Аналитик думает...]")
        try:
            response = session.send_message(user_input)
        except Exception as e:
            print(f"[Ошибка вызова модели: {e}]")
            continue

        print(f"\nАналитик > {response.message}\n")

        if not response.is_complete and response.clarification_questions:
            print("Уточняющие вопросы:")
            for idx, q in enumerate(response.clarification_questions, start=1):
                cat = f"[{q.category.upper()}]"
                print(f"  {idx}. {cat} {q.question}")
                if q.suggestion:
                    print(f"     💡 Предложение: {q.suggestion}")
        elif response.is_complete:
            print("✓ Все требования собраны! Спецификация готова к формированию.")
            if response.identified_sources:
                print(f"  - Источники: {response.identified_sources}")
            if response.identified_operations:
                print(f"  - Операции: {response.identified_operations}")


if __name__ == "__main__":
    main()
