-- Initial schema for a NEW agreed PostgreSQL schema. Do not apply over another team's tables.
CREATE TABLE projects (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE messages (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    role text NOT NULL CHECK (role IN ('user','assistant','system')),
    content text NOT NULL CHECK (length(btrim(content)) > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX messages_project_history ON messages(project_id, created_at, id);

CREATE TABLE specifications (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    version integer NOT NULL CHECK (version > 0),
    spec_json jsonb NOT NULL CHECK (jsonb_typeof(spec_json) = 'object'),
    context_json jsonb NOT NULL CHECK (jsonb_typeof(context_json) = 'object'),
    status text NOT NULL CHECK (status IN ('DRAFT','APPROVED')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(project_id, version)
);

CREATE TABLE verification_runs (
    id uuid PRIMARY KEY,
    spec_id uuid NOT NULL REFERENCES specifications(id),
    status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','FINISHED')),
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    result_json jsonb,
    CHECK ((status = 'FINISHED') = (finished_at IS NOT NULL AND result_json IS NOT NULL))
);
CREATE INDEX verification_queue ON verification_runs(queued_at, id) WHERE status='QUEUED';
CREATE INDEX verification_spec_history ON verification_runs(spec_id, queued_at, id);

CREATE TABLE property_results (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES verification_runs(id),
    property_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('PROPERTY_HOLDS','PROPERTY_VIOLATED')),
    counterexample jsonb,
    stats jsonb,
    error_message text,
    UNIQUE(run_id, property_id)
);

CREATE TABLE artifacts (
    id uuid PRIMARY KEY,
    spec_id uuid NOT NULL REFERENCES specifications(id),
    kind text NOT NULL CHECK (kind IN ('tla','cfg','pyspark','generation_manifest')),
    filename text NOT NULL,
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(spec_id, kind, filename)
);

CREATE FUNCTION keep_specification_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id, NEW.project_id, NEW.version, NEW.spec_json, NEW.context_json, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.project_id, OLD.version, OLD.spec_json, OLD.context_json, OLD.created_at)
       OR (OLD.status = 'APPROVED' AND NEW.status <> OLD.status) THEN
        RAISE EXCEPTION 'Specification snapshots are immutable; create a new version';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER specification_immutable BEFORE UPDATE ON specifications
FOR EACH ROW EXECUTE FUNCTION keep_specification_immutable();

CREATE FUNCTION keep_verification_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'FINISHED' OR NEW.spec_id <> OLD.spec_id OR NEW.id <> OLD.id
       OR NEW.queued_at <> OLD.queued_at
       OR (OLD.status='RUNNING' AND NEW.status='QUEUED') THEN
        RAISE EXCEPTION 'Verification identity and finished results are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER verification_immutable BEFORE UPDATE ON verification_runs
FOR EACH ROW EXECUTE FUNCTION keep_verification_immutable();

CREATE FUNCTION keep_artifact_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.kind <> 'generation_manifest'
       OR (NEW.id, NEW.spec_id, NEW.kind, NEW.filename, NEW.created_at)
          IS DISTINCT FROM (OLD.id, OLD.spec_id, OLD.kind, OLD.filename, OLD.created_at) THEN
        RAISE EXCEPTION 'Issued artifacts are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artifact_immutable BEFORE UPDATE ON artifacts
FOR EACH ROW EXECUTE FUNCTION keep_artifact_immutable();
