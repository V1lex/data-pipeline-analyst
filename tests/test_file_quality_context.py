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


def test_explicit_business_rules_are_checked_and_passed_to_model():
    source_id = uuid4()
    source = {
        "id": source_id,
        "filename": "orders.csv",
        "profile": {
            "columns": [{"name": "id"}, {"name": "price"}],
            "read_options": {"delimiter": ","},
        },
    }

    class Repository:
        def get_source_file(self, project, file_id):
            return {"content": b"id,price\na,10\na,0\n"}

    context = file_quality_context(
        Repository(), uuid4(), [source], "Проверь: price больше 0, id уникален"
    )
    assert '"rule": "gt"' in context
    assert '"rule": "unique"' in context
    assert '"example_rows": [2]' in context


def test_context_lists_only_columns_with_checked_rules():
    source_id = uuid4()
    source = {
        "id": source_id,
        "filename": "orders.csv",
        "profile": {
            "columns": [{"name": "price"}, {"name": "status"}],
            "read_options": {"delimiter": ","},
        },
    }

    class Repository:
        def get_source_file(self, project, file_id):
            return {"content": b"price,status\n1,paid\n"}

    context = file_quality_context(Repository(), uuid4(), [source], "Проверь: price больше 0")
    assert '"checked_columns": ["price"]' in context
