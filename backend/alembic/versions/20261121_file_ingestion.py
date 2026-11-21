"""Persist file-processing identities and local status events."""

from alembic import op

revision = "20261121_file_ingestion"
down_revision = "20261121_source_upload_aliases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE file_ingestions (
        project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        technical_id text NOT NULL,
        source_id uuid NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
        validation jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(project_id, technical_id)
      )
    """)
    op.execute("""
      CREATE TABLE file_ingestion_events (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        technical_id text NOT NULL,
        source_id uuid REFERENCES source_files(id) ON DELETE CASCADE,
        required_columns jsonb NOT NULL,
        branch text NOT NULL CHECK (branch IN ('NEW_VALID','NEW_INVALID','ALREADY_LOADED')),
        status text NOT NULL CHECK (status IN ('SUCCESS','ERROR')),
        validation jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute(
        "CREATE INDEX file_ingestion_events_project ON file_ingestion_events(project_id, created_at, id)"
    )


def downgrade():
    op.execute("DROP TABLE file_ingestion_events")
    op.execute("DROP TABLE file_ingestions")
