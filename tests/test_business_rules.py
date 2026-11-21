import json

import pytest
from file_data.validation import validate_full_file


def test_rules_check_all_rows_and_report_specific_failures(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text("id,price,status\na,10,paid\nb,0,pending\na,abc,unknown\n", encoding="utf-8")
    rules = [
        {"kind": "unique", "column": "id"},
        {"kind": "gt", "column": "price", "value": 0},
        {"kind": "one_of", "column": "status", "values": ["paid", "pending"]},
    ]
    result = validate_full_file(path, path.name, {"encoding": "utf-8", "delimiter": ","}, [], rules)
    assert not result["valid"]
    assert result["checked_rows"] == 3
    issues = {(e["rule"], e["column"]): e for e in result["rule_errors"]}
    assert issues[("unique", "id")]["example_rows"] == [3]
    assert issues[("gt", "price")]["example_rows"] == [2, 3]
    assert issues[("one_of", "status")]["example_rows"] == [3]


def test_rules_do_not_treat_blank_as_numeric_or_duplicate_without_not_null(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps([{"x": None}, {"x": None}, {"x": "3"}]))
    result = validate_full_file(
        path,
        path.name,
        {},
        [],
        [{"kind": "unique", "column": "x"}, {"kind": "number", "column": "x"}],
    )
    assert result["valid"]


def test_chat_rules_are_extracted_only_from_explicit_column_and_condition():
    from file_data.rules import parse_chat_rules

    text = "Проверь: price больше 0, quantity не меньше 1, product_id уникален, city не пустой"
    result = parse_chat_rules(text, ["price", "quantity", "product_id", "city"])
    assert {json.dumps(r, sort_keys=True) for r in result} == {
        json.dumps(r, sort_keys=True)
        for r in [
            {"kind": "gt", "column": "price", "value": 0},
            {"kind": "gte", "column": "quantity", "value": 1},
            {"kind": "unique", "column": "product_id"},
            {"kind": "not_null", "column": "city"},
        ]
    }
    assert parse_chat_rules("проверь продажи", ["price"]) == []


def test_positive_nonnegative_and_iso_date_rules(tmp_path):
    from file_data.rules import parse_chat_rules

    rules = parse_chat_rules(
        "Проверь: price положительный, quantity неотрицательное, date корректная дата",
        ["price", "quantity", "date"],
    )
    assert {rule["kind"] for rule in rules} == {"gt", "gte", "date"}
    path = tmp_path / "x.csv"
    path.write_text("price,quantity,date\n1,0,2024-02-29\n0,-1,2023-02-29\n")
    result = validate_full_file(path, path.name, {}, [], rules)
    assert {(e["rule"], e["column"]) for e in result["rule_errors"]} == {
        ("gt", "price"),
        ("gte", "quantity"),
        ("date", "date"),
    }


@pytest.mark.parametrize("extension", ["jsonl", "parquet"])
def test_rules_read_full_jsonl_and_parquet(tmp_path, extension):
    path = tmp_path / f"sample.{extension}"
    rows = [{"id": i, "price": 10} for i in range(1000)] + [{"id": 1000, "price": -1}]
    if extension == "jsonl":
        path.write_text("\n".join(json.dumps(row) for row in rows))
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(pa.Table.from_pylist(rows), path)
    result = validate_full_file(
        path, path.name, {}, [], [{"kind": "gte", "column": "price", "value": 0}]
    )
    assert result["checked_rows"] == 1001
    assert result["rule_errors"][0]["example_rows"] == [1001]


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "gt", "column": "x", "value": "NaN"},
        {"kind": "execute", "column": "x"},
        {"kind": "one_of", "column": "x", "values": []},
        {"kind": [], "column": "x"},
    ],
)
def test_invalid_rules_are_rejected(tmp_path, rule):
    path = tmp_path / "x.csv"
    path.write_text("x\n1\n")
    with pytest.raises(ValueError):
        validate_full_file(path, path.name, {}, [], [rule])
