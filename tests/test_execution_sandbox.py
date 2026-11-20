import io
import json
import tarfile
from uuid import uuid4

import pytest


def test_execution_bundle_contains_only_selected_inputs_and_code():
    from executor.docker_runner import build_bundle

    file_id = uuid4()
    bundle = build_bundle(
        "print(1)", {file_id: {"format": "csv", "content": b"a,b\n1,2\n"}}, {"--orders": file_id}
    )
    with tarfile.open(fileobj=io.BytesIO(bundle)) as tar:
        assert set(tar.getnames()) == {"config.json", "program.py", file_id.hex + ".csv"}
        config = json.load(tar.extractfile("config.json"))
        assert config["inputs"]["--orders"] == file_id.hex + ".csv"


@pytest.mark.parametrize("flag", ["--output", "--bad flag", "orders", "--foo=bar"])
def test_execution_rejects_untrusted_argument_names(flag):
    from executor.docker_runner import build_bundle

    file_id = uuid4()
    with pytest.raises(ValueError):
        build_bundle(
            "print(1)", {file_id: {"format": "csv", "content": b"a,b\n1,2\n"}}, {flag: file_id}
        )


def test_execution_requires_all_and_only_selected_files():
    from executor.docker_runner import build_bundle

    file_id, other = uuid4(), uuid4()
    with pytest.raises(ValueError):
        build_bundle(
            "print(1)", {file_id: {"format": "csv", "content": b"a,b\n1,2\n"}}, {"--orders": other}
        )


def test_result_reader_previews_allows_parquet_csv_export(tmp_path):
    import zipfile

    import pyarrow as pa
    import pyarrow.parquet as pq
    from execution.result_reader import read_result

    parquet = io.BytesIO()
    pq.write_table(pa.table({"city": ["Москва"], "amount": [300]}), parquet)
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("result/part.parquet", parquet.getvalue())
    output = tmp_path / "output"
    output.mkdir()
    preview = read_result(archive, output)
    assert preview["rows"][0]["amount"] == 300
    assert preview["csv_available"] is True
    assert "Москва,300" in (output / "result.csv").read_text()


def test_result_reader_rejects_path_traversal(tmp_path):
    import zipfile

    from execution.result_reader import read_result

    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("../escape.csv", "a,b\n1,2\n")
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError):
        read_result(archive, output)
    assert not (tmp_path / "escape.csv").exists()


def test_execution_bundle_preserves_explicit_non_file_parameters():
    from executor.docker_runner import build_bundle

    file_id = uuid4()
    bundle = build_bundle(
        "print(1)",
        {file_id: {"format": "csv", "content": b"a,b\n1,2\n"}},
        {"--orders": file_id},
        parameters={"--minimum": "5"},
    )
    with tarfile.open(fileobj=io.BytesIO(bundle)) as tar:
        config = json.load(tar.extractfile("config.json"))
    assert config["parameters"] == {"--minimum": "5"}
    with pytest.raises(ValueError):
        build_bundle(
            "print(1)",
            {file_id: {"format": "csv", "content": b"a,b\n1,2\n"}},
            {"--orders": file_id},
            parameters={"--output": "/etc"},
        )


def test_result_reader_exports_csv_parts_without_duplicate_header(tmp_path):
    import zipfile

    from execution.result_reader import read_result

    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("result/part-1.csv", "city,amount\nМосква,300\n")
        out.writestr("result/part-2.csv", "city,amount\nКазань,150\n")
    output = tmp_path / "output"
    output.mkdir()
    preview = read_result(archive, output)
    assert preview["csv_available"] is True
    assert (output / "result.csv").read_text().count("city,amount") == 1
    assert "Казань,150" in (output / "result.csv").read_text()


def test_result_reader_previews_and_exports_spark_json_lines(tmp_path):
    import zipfile

    from execution.result_reader import read_result

    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "part-00000.json", '{"city":"Москва","amount":300}\n{"city":"Казань","amount":150}\n'
        )
    output = tmp_path / "output"
    output.mkdir()
    preview = read_result(archive, output)
    assert preview["rows"][1]["city"] == "Казань"
    assert preview["csv_available"]
    assert "Казань,150" in (output / "result.csv").read_text()


def test_result_preview_includes_later_parquet_part_when_first_is_empty(tmp_path):
    import zipfile

    import pyarrow as pa
    import pyarrow.parquet as pq
    from execution.result_reader import read_result

    schema = pa.schema([("value", pa.int64())])
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for filename, values in [("part-1.parquet", []), ("part-2.parquet", [7])]:
            buffer = io.BytesIO()
            pq.write_table(pa.table({"value": values}, schema=schema), buffer)
            out.writestr("result/" + filename, buffer.getvalue())
    output = tmp_path / "output"
    output.mkdir()
    assert read_result(archive, output)["rows"] == [{"value": 7}]
