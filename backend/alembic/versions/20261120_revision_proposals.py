"""Persist user-reviewed formal-model revision proposals."""

from alembic import op

revision = "20261120_revision_proposals"
down_revision = "20261120_execution_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE revision_proposals (
        id uuid PRIMARY KEY,
        spec_id uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
        run_id uuid NOT NULL REFERENCES verification_runs(id) ON DELETE CASCADE,
        base_version integer NOT NULL,
        proposal jsonb NOT NULL,
        status text NOT NULL CHECK (status IN ('PROPOSED','ACCEPTED','REJECTED')),
        applied_spec_id uuid REFERENCES specifications(id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK ((status='ACCEPTED') = (applied_spec_id IS NOT NULL))
      )
    """)
    op.execute(
        "CREATE UNIQUE INDEX one_pending_proposal_per_run ON revision_proposals(run_id) WHERE status='PROPOSED'"
    )
    op.execute("""
      CREATE FUNCTION keep_revision_proposal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF (NEW.id,NEW.spec_id,NEW.run_id,NEW.base_version,NEW.proposal,NEW.created_at)
           IS DISTINCT FROM (OLD.id,OLD.spec_id,OLD.run_id,OLD.base_version,OLD.proposal,OLD.created_at)
           OR OLD.status <> 'PROPOSED' THEN
          RAISE EXCEPTION 'Revision proposal is immutable';
        END IF;
        RETURN NEW;
      END $$
    """)
    op.execute(
        "CREATE TRIGGER revision_proposal_immutable BEFORE UPDATE ON revision_proposals FOR EACH ROW EXECUTE FUNCTION keep_revision_proposal_immutable()"
    )


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to remove revision proposals")
