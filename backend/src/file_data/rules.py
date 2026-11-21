"""Bounded, explicit data-quality rules; never execute user expressions."""

import re
from decimal import Decimal, InvalidOperation


def decimal_value(value):
    if isinstance(value, bool) or value is None or len(str(value)) > 100:
        return None
    try:
        number = Decimal(str(value).strip())
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def validate_rules(rules):
    if not isinstance(rules, list) or len(rules) > 20:
        raise ValueError("Допускается не более 20 правил.")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Некорректное правило.")
        kind, column = rule.get("kind"), rule.get("column")
        if not isinstance(kind, str):
            raise ValueError("Некорректный тип правила.")
        if not isinstance(column, str) or not column.strip() or len(column) > 256:
            raise ValueError("Некорректное имя колонки.")
        if kind in {"not_null", "unique", "number", "date"}:
            keys = {"kind", "column"}
        elif kind in {"gt", "gte", "lt", "lte"}:
            keys = {"kind", "column", "value"}
            if decimal_value(rule.get("value")) is None:
                raise ValueError("Для сравнения укажите конечное число.")
        elif kind == "one_of":
            keys = {"kind", "column", "values"}
            values = rule.get("values")
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 100
                or any(not isinstance(value, str) or len(value) > 256 for value in values)
            ):
                raise ValueError("Укажите список допустимых значений.")
        else:
            raise ValueError("Неподдерживаемое правило.")
        if set(rule) != keys:
            raise ValueError("Некорректные параметры правила.")
        signature = repr(sorted(rule.items()))
        if signature in seen:
            raise ValueError("Повторяющееся правило.")
        seen.add(signature)
    return rules


def parse_chat_rules(text, columns):
    """Recognise only an explicit field followed immediately by a known rule."""
    result = []
    number = r"([-+]?\d+(?:[.,]\d+)?)"
    for column in columns:
        if not isinstance(column, str) or not column:
            continue
        prefix = (
            r"(?<![\w])"
            + re.escape(column)
            + r"(?![\w])\s*(?:(?:должен|должна|должно|должны)\s+(?:быть\s+)?)?"
        )
        conditions = [
            ("not_null", r"(?:не\s+пуст\w*|обязательн\w*|not\s+null)"),
            ("unique", r"(?:уникальн\w*|уникален|без\s+дубликатов|unique)"),
            ("number", r"(?:числ\w*|числов\w*|number|numeric)"),
            ("date", r"(?:(?:корректн\w*\s+)?дата|date)"),
            ("positive", r"положительн\w*"),
            ("nonnegative", r"неотрицательн\w*"),
            ("gte", r"(?:не\s+меньше|больше\s+или\s+равно|>=)\s*" + number),
            ("lte", r"(?:не\s+больше|меньше\s+или\s+равно|<=)\s*" + number),
            ("gt", r"(?:больше|строго\s+больше|>)\s*" + number),
            ("lt", r"(?:меньше|строго\s+меньше|<)\s*" + number),
        ]
        for kind, expression in conditions:
            for match in re.finditer(prefix + expression, text, flags=re.IGNORECASE):
                rule = {"kind": kind, "column": column}
                if kind in {"positive", "nonnegative"}:
                    rule.update({"kind": "gt" if kind == "positive" else "gte", "value": 0})
                if kind in {"gt", "gte", "lt", "lte"}:
                    rule["value"] = match.group(1).replace(",", ".")
                    if "." not in rule["value"]:
                        rule["value"] = int(rule["value"])
                if rule not in result:
                    result.append(rule)
    return validate_rules(result)
