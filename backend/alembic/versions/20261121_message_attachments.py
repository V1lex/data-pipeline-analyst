"""Record attachments belonging to each dialogue message."""

from alembic import op

revision = "20261121_message_attachments"
down_revision = "20261120_revision_proposals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE messages ADD COLUMN attachments jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade():
    op.execute("ALTER TABLE messages DROP COLUMN attachments")
