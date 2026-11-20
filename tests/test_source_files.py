import pytest
from pipeline_generator.errors import PipelineError
from psycopg import errors

pytestmark = pytest.mark.postgres


def test_source_file_is_project_bound_and_immutable(repository):
    project = repository.create_project("one")
    other = repository.create_project("two")
    source = repository.store_source_file(
        project, "data.csv", b"a\n1\n", {"format": "csv"}, max_files=2
    )
    assert repository.get_source_file(project, source)["content"] == b"a\n1\n"
    assert len(repository.list_source_files(project)) == 1
    assert repository.list_source_files(other) == []
    with pytest.raises(PipelineError):
        repository.get_source_file(other, source)
    with repository.connect() as connection, pytest.raises(errors.RaiseException):
        connection.execute("UPDATE source_files SET content='x' WHERE id=%s", (source,))
    repository.delete_project(project)
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM source_files WHERE id=%s", (source,)
            ).fetchone()["n"]
            == 0
        )


def test_source_file_count_limit(repository):
    project = repository.create_project("limit")
    repository.store_source_file(project, "one.csv", b"a\n1\n", {}, max_files=1)
    with pytest.raises(PipelineError, match="FILE_LIMIT"):
        repository.store_source_file(project, "two.csv", b"a\n2\n", {}, max_files=1)
