import os
from uuid import uuid4

import psycopg
import pytest
from pipeline_generator.storage import PostgresRepository
from psycopg import sql


@pytest.fixture
def empty_schema():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required")
    name = "test_migration_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield PostgresRepository(dsn, schema=name)
    finally:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def test_alembic_is_single_schema_owner(empty_schema):
    from db.migrations import upgrade

    upgrade(empty_schema.dsn, schema=empty_schema.schema)
    empty_schema.migrate()  # repeat is an Alembic no-op
    project = empty_schema.create_project("MVP")
    with empty_schema.connect() as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()
        assert (
            con.execute("SELECT name FROM projects WHERE id=%s", (project,)).fetchone()["name"]
            == "MVP"
        )


def test_upgrade_preserves_initial_data_without_inventing_context(empty_schema):
    from db.migrations import upgrade

    upgrade(empty_schema.dsn, schema=empty_schema.schema, revision="12a5814e76d1")
    project, spec = uuid4(), uuid4()
    with empty_schema.connect() as con:
        con.execute("INSERT INTO projects VALUES (%s,'Legacy',NULL,now())", (project,))
        con.execute(
            "INSERT INTO specifications VALUES (%s,%s,1,'{}','confirmed',now())", (spec, project)
        )
    upgrade(empty_schema.dsn, schema=empty_schema.schema)
    row = empty_schema.get_specification(spec)
    assert row["status"] == "APPROVED"
    assert row["context_json"] is None


def test_dialogue_lease_prevents_concurrent_turns_and_persists_atomically(repository):
    from pipeline_generator.errors import PipelineError

    project = repository.create_project("Chat")
    lease, snapshot = repository.begin_dialogue(project)
    assert snapshot == {}
    with pytest.raises(PipelineError, match=".*") as failure:
        repository.begin_dialogue(project)
    assert failure.value.code == "DIALOGUE_BUSY"
    repository.commit_dialogue(project, lease, {"history": []}, user="Hello", assistant="Hi")
    assert [m["content"] for m in repository.list_messages(project)] == ["Hello", "Hi"]
    lease, snapshot = repository.begin_dialogue(project)
    assert snapshot == {"history": []}
    repository.abort_dialogue(project, lease)


def test_unversioned_populated_schema_is_refused_without_data_loss(empty_schema):
    from db.migrations import upgrade

    with empty_schema.connect() as con:
        con.execute("CREATE TABLE projects (id integer PRIMARY KEY, name text)")
        con.execute("INSERT INTO projects VALUES (1,'Preserve me')")
    with pytest.raises(RuntimeError, match="Unversioned existing pipeline schema"):
        upgrade(empty_schema.dsn, schema=empty_schema.schema)
    with empty_schema.connect() as con:
        assert con.execute("SELECT name FROM projects").fetchone()["name"] == "Preserve me"
        assert (
            con.execute("SELECT to_regclass('alembic_version') as version").fetchone()["version"]
            is None
        )
