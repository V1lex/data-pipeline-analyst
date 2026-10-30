#!/usr/bin/env python3
"""Interactive CLI dialogue with Data Pipeline Analyst with Completeness Verification."""

import sys
from analyst.completeness import ParameterOrigin, AgreementStatus
from analyst.session import AnalystSession


def main() -> None:
    print("=" * 75)
    print("ИИ-Аналитик конвейеров данных (Data Pipeline Analyst) — Режим диалога (Т13)")
    print("Поддержка матрицы обязательных параметров операций и согласования предложений.")
    print("Введите ваш запрос по пайплайну данных (или 'exit' для выхода, 'reset' для сброса):")
    print("=" * 75)

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
            print("История диалога и черновик очищены.")
            continue

        print("\n[Аналитик анализирует запрос и проверяет обязательные параметры...]")
        try:
            response = session.send_message(user_input)
        except Exception as e:
            print(f"[Ошибка: {e}]")
            continue

        print(f"\nАналитик > {response.message}\n")

        # Report status
        report = session.get_completeness_report()

        if report.pending_suggestions:
            print("⏳ Предложения аналитика, ожидающие вашего согласования:")
            for idx, p in enumerate(report.pending_suggestions, start=1):
                print(f"  {idx}. [ПРЕДЛОЖЕНИЕ АНАЛИТИКА] '{p.field_name}': {p.value}")
                if p.description:
                    print(f"     Контекст: {p.description}")

        if not response.is_complete and response.clarification_questions:
            print("\n❓ Необходимые уточнения обязательных параметров:")
            for idx, q in enumerate(response.clarification_questions, start=1):
                cat = f"[{q.category.upper()}]"
                print(f"  {idx}. {cat} {q.question}")
                if q.suggestion:
                    print(f"     💡 Предложение: {q.suggestion}")

        elif response.is_complete:
            print("✓ ВСЕ ОБЯЗАТЕЛЬНЫЕ ПАРАМЕТРЫ ЗАПОЛНЕНЫ И СОГЛАСОВАНЫ!")
            print("  Спецификация полностью готова к генерации кода и проверке.")


if __name__ == "__main__":
    main()
