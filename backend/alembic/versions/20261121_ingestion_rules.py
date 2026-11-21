"""Persist business rules alongside ingestion requests."""

from alembic import op

revision = "20261121_ingestion_rules"
down_revision = "20261121_file_ingestion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE file_ingestion_events ADD COLUMN rules jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade():
    op.execute("ALTER TABLE file_ingestion_events DROP COLUMN rules")
