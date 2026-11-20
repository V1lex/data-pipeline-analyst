"""Store immutable project-owned source files and profiles."""

from alembic import op

revision = "20261120_source_files"
down_revision = "20261120_worker_lease"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE source_files (
          id uuid PRIMARY KEY,
          project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          filename text NOT NULL,
          format text NOT NULL CHECK (format IN ('csv','json','jsonl','parquet')),
          size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
          checksum text NOT NULL,
          content bytea NOT NULL,
          profile jsonb NOT NULL,
          status text NOT NULL DEFAULT 'READY' CHECK (status = 'READY'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE INDEX source_files_project_created ON source_files(project_id,created_at,id)"
    )
    op.execute("""
        CREATE FUNCTION keep_source_file_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Source files are immutable'; END $$
    """)
    op.execute(
        "CREATE TRIGGER source_file_immutable BEFORE UPDATE ON source_files FOR EACH ROW EXECUTE FUNCTION keep_source_file_immutable()"
    )


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to remove source files")
