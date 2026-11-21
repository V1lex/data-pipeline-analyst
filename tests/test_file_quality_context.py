from uuid import uuid4

from agent.workflow import file_quality_context


def test_empty_values_request_checks_full_selected_file():
    source_id = uuid4()
    source = {
        "id": source_id,
        "filename": "orders.csv",
        "profile": {
            "columns": [{"name": "price"}],
            "read_options": {"encoding": "utf-8", "delimiter": ","},
        },
    }

    class Repository:
        def get_source_file(self, project, file_id):
            assert file_id == source_id
            return {"content": ("price\n" + "5\n" * 1000 + '""\n').encode()}

    context = file_quality_context(
        Repository(), uuid4(), [source], "Проверь пустые значения в файле"
    )
    assert "1001" in context
    assert "price" in context
    assert "example_rows" in context


def test_non_validation_request_does_not_scan_file():
    class Repository:
        def get_source_file(self, *args):
            raise AssertionError("Should not read a full file for an unrelated request")

    assert file_quality_context(Repository(), uuid4(), [], "Привет") == ""
