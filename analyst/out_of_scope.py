from __future__ import annotations

import re
from typing import NamedTuple

from pipeline_spec.models.enums import OperationType

SUPPORTED_OPERATIONS = {op.value for op in OperationType}

UNSUPPORTED_PATTERNS = {
    "machine_learning": [
        r"обучи.*модел",
        r"градиентн.*бустинг",
        r"линейн.*регресси",
        r"нейросет",
        r"кластеризаци",
        r"train.*model",
        r"fit.*predict",
    ],
    "streaming_windowing": [
        r"streaming",
        r"sliding\s+window",
        r"потоков.*обработк",
        r"kafka\s+stream",
        r"watermark",
    ],
    "graph_processing": [
        r"граф.*вычислен",
        r"поиск\s+кратчайшего\s+пути",
        r"page\s*rank",
        r"dijkstra",
    ],
    "unstructured_nlp": [
        r"распознай\s+аудио",
        r"распарси\s+pdf",
        r"speech\s+to\s+text",
        r"computer\s+vision",
    ],
}


class OutOfScopeError(ValueError):
    """Raised when the user request contains features outside MVP."""
    pass


class ScopeCheckResult(NamedTuple):
    is_in_scope: bool
    unsupported_feature: str | None = None
    reason: str | None = None


def check_scope(user_text: str) -> ScopeCheckResult:
    """Checks whether the requested functionality is within the MVP pipeline scope."""
    text_lower = user_text.lower()

    for feature_category, patterns in UNSUPPORTED_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                return ScopeCheckResult(
                    is_in_scope=False,
                    unsupported_feature=feature_category,
                    reason=(
                        f"Запрос содержит функционал '{feature_category}', выходящий за рамки MVP. "
                        f"Поддерживаются только операции табличной обработки данных: "
                        f"{', '.join(sorted(SUPPORTED_OPERATIONS))}."
                    ),
                )

    return ScopeCheckResult(is_in_scope=True)
