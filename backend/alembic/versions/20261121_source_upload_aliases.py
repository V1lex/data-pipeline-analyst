"""Keep retry identifiers for uploads deduplicated against an existing file."""

from alembic import op

revision = "20261121_source_upload_aliases"
down_revision = "20261121_message_attachments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE source_upload_aliases (
            request_id uuid PRIMARY KEY,
            source_id uuid NOT NULL REFERENCES source_files(id) ON DELETE CASCADE
        )
    """)


def downgrade():
    op.execute("DROP TABLE source_upload_aliases")
