"""Persist selected source IDs for the next dialogue and specification."""

from alembic import op

revision = "20261120_source_selection"
down_revision = "20261120_source_files"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE projects ADD COLUMN selected_source_ids jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to downgrade selected sources")
