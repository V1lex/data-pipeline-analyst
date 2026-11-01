from alembic import context
from db.base import Base
from db import models  # noqa: F401

config = context.config


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema=config.attributes.get("schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    import os
    import re
    from pathlib import Path
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    schema = os.getenv("DATABASE_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid PostgreSQL schema")
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=NullPool)
    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.commit()
        config.attributes["schema"] = schema
        run(connection)
