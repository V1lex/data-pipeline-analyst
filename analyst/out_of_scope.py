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

NEGATION_PREFIXES = [
    r"не\s+(?:нужен|нужно|надо|требуется|используй|использовать|будем|хотим|делай|применяй)",
    r"отка[зж](?:ались|аться|ись|итесь|ался)?\s+от",
    r"\bбез\b",
    r"\bникаких\b",
    r"\bвместо\b",
    r"\bno\b",
    r"\bnot\b",
    r"\bwithout\b",
]

NEGATION_SUFFIXES = [
    r"не\s+(?:нужен|нужно|требуется|надо|будет|используется)",
    r"(?:is\s+)?not\s+(?:needed|required)",
    r"отменяется",
    r"исключить",
    r"заменен\s+на\s+batch",
]


class OutOfScopeError(ValueError):
    """Raised when the user request contains features outside MVP."""
    pass


class ScopeCheckResult(NamedTuple):
    is_in_scope: bool
    unsupported_feature: str | None = None
    reason: str | None = None


def is_negated_match(text: str, start: int, end: int) -> bool:
    """Checks whether the pattern match is negated in its surrounding context."""
    prefix = text[max(0, start - 40):start]
    suffix = text[end:min(len(text), end + 40)]

    for np in NEGATION_PREFIXES:
        if re.search(np, prefix, re.IGNORECASE):
            return True

    for ns in NEGATION_SUFFIXES:
        if re.search(ns, suffix, re.IGNORECASE):
            return True

    return False


def check_dialogue_scope(user_messages: list[str]) -> ScopeCheckResult:
    """Checks scope across user dialogue turns, accounting for decision changes (A10).
    If a user previously asked for an unsupported feature (e.g. streaming), but in a later
    turn negated it, switched to batch, or cancelled it, the feature is not considered active.
    """
    active_features: dict[str, str] = {}

    for msg in user_messages:
        msg_lower = msg.lower()
        for feature_category, patterns in UNSUPPORTED_PATTERNS.items():
            for pat in patterns:
                for match in re.finditer(pat, msg_lower):
                    if is_negated_match(msg_lower, match.start(), match.end()):
                        # User retracted/negated this feature in this turn
                        active_features.pop(feature_category, None)
                    else:
                        active_features[feature_category] = (
                            f"Запрос содержит функционал '{feature_category}', выходящий за рамки MVP. "
                            f"Поддерживаются только операции табличной обработки данных: "
                            f"{', '.join(sorted(SUPPORTED_OPERATIONS))}."
                        )

    if active_features:
        feat, reason = next(iter(active_features.items()))
        return ScopeCheckResult(
            is_in_scope=False,
            unsupported_feature=feat,
            reason=reason,
        )

    return ScopeCheckResult(is_in_scope=True)


def check_scope(user_text: str) -> ScopeCheckResult:
    """Checks whether the requested functionality is within the MVP pipeline scope,
    safely handling negations (e.g. 'streaming не нужен', 'без нейросетей').
    """
    return check_dialogue_scope([user_text])
