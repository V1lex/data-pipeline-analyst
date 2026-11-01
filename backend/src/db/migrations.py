"""Canonical, explicit Alembic entry point for API/worker and isolated tests."""

import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool


def upgrade(dsn: str, *, schema: str = "public", revision: str = "head") -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid PostgreSQL schema")
    backend = Path(__file__).resolve().parents[2]
    if not (backend / "alembic.ini").exists():
        backend = Path(__file__).resolve().parent
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            tables = set(inspect(connection).get_table_names(schema=schema))
            if "alembic_version" not in tables and tables.intersection(
                {
                    "projects",
                    "messages",
                    "specifications",
                    "verification_runs",
                    "artifacts",
                    "property_results",
                }
            ):
                raise RuntimeError(
                    "Unversioned existing pipeline schema: automatic adoption is refused. Back up the database and perform an explicit schema/data migration; no records were changed."
                )
            connection.commit()
            config.attributes["connection"] = connection
            config.attributes["schema"] = schema
            command.upgrade(config, revision)
    finally:
        engine.dispose()


def main():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    upgrade(os.environ["DATABASE_URL"], schema=os.getenv("DATABASE_SCHEMA", "public"))


if __name__ == "__main__":
    main()
