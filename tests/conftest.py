import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


@pytest.fixture
def repository():
    from pipeline_generator.storage import PostgresRepository

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    schema = "test_generator_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = PostgresRepository(dsn, schema=schema)
        repo.migrate()
        yield repo
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
