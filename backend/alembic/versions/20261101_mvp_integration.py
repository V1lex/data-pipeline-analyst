"""Unify team persistence contracts and persist dialogue snapshots.

Existing records are preserved. Missing historic context/verdicts are not invented.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261101_mvp"
down_revision = "12a5814e76d1"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in [
        ("specifications", "status"),
        ("verification_runs", "status"),
        ("artifacts", "kind"),
        ("property_results", "status"),
        ("messages", "role"),
    ]:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text USING {column}::text")
    op.execute(
        "UPDATE specifications SET status=CASE status WHEN 'draft' THEN 'DRAFT' ELSE 'APPROVED' END"
    )
    op.execute(
        "UPDATE verification_runs SET status=CASE status WHEN 'queued' THEN 'QUEUED' WHEN 'running' THEN 'RUNNING' ELSE 'FINISHED' END"
    )
    op.execute("ALTER TABLE specifications ADD COLUMN context_json jsonb")
    op.execute("ALTER TABLE verification_runs ADD COLUMN result_json jsonb")
    # Legacy completed runs have no trusted normalized verdict. Keep them blocked.
    op.execute(
        'UPDATE verification_runs SET result_json=\'{"status":"ENGINE_ERROR","engine":"TLC","error":"Legacy run has no persisted verdict"}\', finished_at=COALESCE(finished_at,now()) WHERE status=\'FINISHED\''
    )
    for table, column in [
        ("projects", "created_at"),
        ("messages", "created_at"),
        ("specifications", "created_at"),
        ("artifacts", "created_at"),
        ("verification_runs", "queued_at"),
    ]:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT clock_timestamp()")
    op.execute("UPDATE projects SET description='' WHERE description IS NULL")
    op.execute("ALTER TABLE projects ALTER COLUMN description SET DEFAULT ''")
    op.execute("ALTER TABLE projects ALTER COLUMN description SET NOT NULL")
    for table, name, condition in [
        ("specifications", "spec_status_valid", "status IN ('DRAFT','APPROVED')"),
        ("verification_runs", "run_status_valid", "status IN ('QUEUED','RUNNING','FINISHED')"),
        (
            "verification_runs",
            "finished_result_valid",
            "(status='FINISHED') = (finished_at IS NOT NULL AND result_json IS NOT NULL)",
        ),
        (
            "artifacts",
            "artifact_kind_valid",
            "kind IN ('tla','cfg','pyspark','generation_manifest')",
        ),
        ("messages", "message_role_valid", "role IN ('user','assistant','system')"),
        ("specifications", "positive_version", "version > 0"),
    ]:
        op.create_check_constraint(name, table, condition)
    op.create_unique_constraint(
        "unique_artifact_filename", "artifacts", ["spec_id", "kind", "filename"]
    )
    op.create_index(
        "verification_spec_history", "verification_runs", ["spec_id", "queued_at", "id"]
    )
    op.execute(
        "ALTER TABLE projects ADD COLUMN analyst_state jsonb NOT NULL DEFAULT '{}', ADD COLUMN state_revision integer NOT NULL DEFAULT 0, ADD COLUMN lease_token uuid, ADD COLUMN lease_until timestamptz"
    )
    op.execute(TRIGGERS)


def downgrade():
    raise RuntimeError("MVP snapshots cannot be downgraded safely; restore a pre-migration backup")


TRIGGERS = "CREATE FUNCTION keep_specification_immutable() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n    IF (NEW.id, NEW.project_id, NEW.version, NEW.spec_json, NEW.context_json, NEW.created_at)\n       IS DISTINCT FROM\n       (OLD.id, OLD.project_id, OLD.version, OLD.spec_json, OLD.context_json, OLD.created_at)\n       OR (OLD.status = 'APPROVED' AND NEW.status <> OLD.status) THEN\n        RAISE EXCEPTION 'Specification snapshots are immutable; create a new version';\n    END IF;\n    RETURN NEW;\nEND $$;\nCREATE TRIGGER specification_immutable BEFORE UPDATE ON specifications\nFOR EACH ROW EXECUTE FUNCTION keep_specification_immutable();\n\nCREATE FUNCTION keep_verification_immutable() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n    IF OLD.status = 'FINISHED' OR NEW.spec_id <> OLD.spec_id OR NEW.id <> OLD.id\n       OR NEW.queued_at <> OLD.queued_at\n       OR (OLD.status='RUNNING' AND NEW.status='QUEUED') THEN\n        RAISE EXCEPTION 'Verification identity and finished results are immutable';\n    END IF;\n    RETURN NEW;\nEND $$;\nCREATE TRIGGER verification_immutable BEFORE UPDATE ON verification_runs\nFOR EACH ROW EXECUTE FUNCTION keep_verification_immutable();\n\nCREATE FUNCTION keep_artifact_immutable() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n    IF OLD.kind <> 'generation_manifest'\n       OR (NEW.id, NEW.spec_id, NEW.kind, NEW.filename, NEW.created_at)\n          IS DISTINCT FROM (OLD.id, OLD.spec_id, OLD.kind, OLD.filename, OLD.created_at) THEN\n        RAISE EXCEPTION 'Issued artifacts are immutable';\n    END IF;\n    RETURN NEW;\nEND $$;\nCREATE TRIGGER artifact_immutable BEFORE UPDATE ON artifacts\nFOR EACH ROW EXECUTE FUNCTION keep_artifact_immutable();\n"
