import json

import pytest


def test_csv_profile_handles_cyrillic_quotes_newlines_and_missing(tmp_path):
    from file_data.profile import profile_file

    path = tmp_path / "data.csv"
    path.write_text('город;сумма\n"Новый\nгород";12\nМосква;\n', encoding="utf-8")
    profile = profile_file(path, "data.csv", "hash")
    assert profile["format"] == "csv"
    assert profile["read_options"]["delimiter"] == ";"
    assert profile["sample"][0]["город"] == "Новый\nгород"
    assert profile["columns"][1]["null_count"] == 1
    assert profile["scope"] == "sample"


@pytest.mark.parametrize(
    "suffix,content",
    [("json", '[{"city":"Москва","amount":1}]'), ("jsonl", '{"city":"Москва","amount":1}\n')],
)
def test_flat_json_profiles(tmp_path, suffix, content):
    from file_data.profile import profile_file

    path = tmp_path / ("data." + suffix)
    path.write_text(content, encoding="utf-8")
    result = profile_file(path, path.name, "hash")
    assert result["sample"][0]["city"] == "Москва"
    assert {item["name"] for item in result["columns"]} == {"city", "amount"}


def test_nested_json_is_rejected(tmp_path):
    from file_data.profile import ProfileError, profile_file

    path = tmp_path / "nested.json"
    path.write_text(json.dumps([{"a": {"b": 1}}]))
    with pytest.raises(ProfileError, match="влож"):
        profile_file(path, path.name, "hash")


def test_parquet_uses_recorded_schema(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from file_data.profile import profile_file

    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"city": ["Москва"], "amount": [12]}), path)
    result = profile_file(path, path.name, "hash")
    assert result["columns"][1]["type"] == "int64"
    assert result["sample"][0]["amount"] == 12


def test_cp1251_csv_and_explicit_delimiter(tmp_path):
    from file_data.profile import profile_file

    path = tmp_path / "data.csv"
    path.write_bytes("город;сумма\nМосква;10\n".encode("cp1251"))
    profile = profile_file(path, path.name, "hash", {"encoding": "cp1251", "delimiter": ";"})
    assert profile["sample"][0]["город"] == "Москва"


def test_csv_detects_cp1251_and_semicolon_automatically(tmp_path):
    from file_data.profile import profile_file

    path = tmp_path / "data.csv"
    path.write_bytes("город;сумма\nМосква;10\n".encode("cp1251"))
    profile = profile_file(path, path.name, "hash")
    assert profile["read_options"] == {"encoding": "cp1251", "delimiter": ";"}
    assert profile["sample"][0]["город"] == "Москва"


def test_csv_accepts_single_column_without_delimiter(tmp_path):
    from file_data.profile import profile_file

    path = tmp_path / "data.csv"
    path.write_text("город\nМосква\nКазань\n", encoding="utf-8")
    profile = profile_file(path, path.name, "hash")
    assert profile["sample"] == [{"город": "Москва"}, {"город": "Казань"}]


@pytest.mark.parametrize(
    "filename, content",
    [
        ("bad.csv", b"a,b\n1,2,3\n"),
        ("bad.json", b'[{"a":'),
        ("bad.jsonl", b'{"a":1}\nnot-json\n'),
        ("bad.parquet", b"not parquet"),
    ],
)
def test_corrupt_sources_fail_safely(tmp_path, filename, content):
    from file_data.profile import ProfileError, profile_file

    path = tmp_path / filename
    path.write_bytes(content)
    with pytest.raises(ProfileError):
        profile_file(path, filename, "hash")


def test_nested_record_beyond_sample_is_rejected(tmp_path):
    from file_data.profile import ProfileError, profile_file

    path = tmp_path / "nested.jsonl"
    path.write_text(("{}\n" * 1000) + '{"a": []}\n')
    with pytest.raises(ProfileError, match="влож"):
        profile_file(path, path.name, "hash")


def test_profile_subprocess_returns_profile_without_provider_secrets(tmp_path, monkeypatch):
    from file_data.profile import profile_in_subprocess

    path = tmp_path / "table.csv"
    path.write_text("a,b\n1,2\n")
    monkeypatch.setenv("POLZA_API_KEY", "not-a-real-key")
    result = profile_in_subprocess(path, path.name, "hash")
    assert result["sample"][0] == {"a": "1", "b": "2"}
    assert "not-a-real-key" not in json.dumps(result)


def test_parquet_profile_in_bounded_process(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from file_data.profile import profile_in_subprocess

    path = tmp_path / "input.parquet"
    pq.write_table(pa.table({"value": [5, 6]}), path)
    result = profile_in_subprocess(path, path.name, "hash")
    assert result["sample"][1]["value"] == 6
