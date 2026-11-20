"""Store isolated computation attempts separately from formal verification."""

from alembic import op

revision = "20261120_execution_runs"
down_revision = "20261120_source_selection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE execution_runs (
          id uuid PRIMARY KEY,
          spec_id uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
          artifact_id uuid NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
          verification_run_id uuid NOT NULL REFERENCES verification_runs(id) ON DELETE CASCADE,
          inputs jsonb NOT NULL,
          parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
          status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','TIMEOUT')),
          queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          started_at timestamptz,
          finished_at timestamptz,
          lease_token uuid,
          lease_until timestamptz,
          error text,
          result_archive bytea,
          result_csv bytea,
          result_preview jsonb,
          CHECK ((status='RUNNING') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
          CHECK ((status IN ('SUCCEEDED','FAILED','TIMEOUT')) = (finished_at IS NOT NULL)),
          CHECK ((status='SUCCEEDED') = (result_archive IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX execution_runs_queue ON execution_runs(status,queued_at,id)")
    op.execute("""
        CREATE FUNCTION keep_execution_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.id,NEW.spec_id,NEW.artifact_id,NEW.verification_run_id,NEW.inputs,NEW.parameters,NEW.queued_at)
             IS DISTINCT FROM (OLD.id,OLD.spec_id,OLD.artifact_id,OLD.verification_run_id,OLD.inputs,OLD.parameters,OLD.queued_at)
             OR OLD.status IN ('SUCCEEDED','FAILED','TIMEOUT') THEN
            RAISE EXCEPTION 'Execution identity and finished results are immutable';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER execution_immutable BEFORE UPDATE ON execution_runs FOR EACH ROW EXECUTE FUNCTION keep_execution_immutable()"
    )


def downgrade():
    raise RuntimeError("Restore a pre-migration backup to remove execution history")
