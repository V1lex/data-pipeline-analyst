"""Add expiring ownership for verification attempts."""

from alembic import op

revision = "20261120_worker_lease"
down_revision = "20261120_check_status"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE verification_runs ADD COLUMN lease_token uuid, ADD COLUMN lease_until timestamptz, ADD COLUMN heartbeat_at timestamptz"
    )
    # Existing running jobs have no living owner after deployment and are reclaimable.
    op.execute(
        "UPDATE verification_runs SET lease_token=gen_random_uuid(), lease_until=clock_timestamp()-interval '1 second', heartbeat_at=clock_timestamp() WHERE status='RUNNING'"
    )
    op.create_check_constraint(
        "verification_lease_valid",
        "verification_runs",
        "(status='RUNNING' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND heartbeat_at IS NOT NULL) OR (status<>'RUNNING' AND lease_token IS NULL AND lease_until IS NULL AND heartbeat_at IS NULL)",
    )


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to downgrade verification leases")
