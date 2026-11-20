"""Upgrade persisted formal-check statuses without rewriting history."""

from alembic import op

revision = "20261120_check_status"
down_revision = "20261101_mvp"
branch_labels = None
depends_on = None


def upgrade():
    # The MVP revision converted the column to text, but retains the enum type.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
                     JOIN pg_namespace n ON n.oid=t.typnamespace
                     WHERE n.nspname=current_schema() AND t.typname='check_status'
                       AND e.enumlabel='ENGINE_ERROR') THEN
            ALTER TYPE check_status RENAME VALUE 'ENGINE_ERROR' TO 'INFRASTRUCTURE_ERROR';
          END IF;
        END $$
    """)
    op.execute("ALTER TYPE check_status ADD VALUE IF NOT EXISTS 'PROPERTY_UNREACHABLE'")
    op.execute(
        "UPDATE property_results SET status='INFRASTRUCTURE_ERROR' WHERE status='ENGINE_ERROR'"
    )
    # Finished results are immutable during normal operation; this migration only
    # normalizes the legacy error label, preserving the rest of each verdict.
    op.execute("ALTER TABLE verification_runs DISABLE TRIGGER verification_immutable")
    op.execute("""
        UPDATE verification_runs
        SET result_json = jsonb_set(result_json, '{status}', '"INFRASTRUCTURE_ERROR"'::jsonb)
        WHERE result_json->>'status' = 'ENGINE_ERROR'
    """)
    op.execute("ALTER TABLE verification_runs ENABLE TRIGGER verification_immutable")


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to downgrade formal-check statuses")
