CREATE TABLE IF NOT EXISTS authoring_draft (
  id text PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspace(id),
  created_by text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('create', 'revise')),
  status text NOT NULL CHECK (status IN ('editing', 'submitted', 'abandoned')),
  entity_id text NOT NULL,
  version integer NOT NULL CHECK (version >= 1),
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS authoring_draft_workspace_updated_idx
  ON authoring_draft (workspace_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS authoring_draft_entity_idx
  ON authoring_draft (entity_id, status);

ALTER TABLE authoring_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE authoring_draft FORCE ROW LEVEL SECURITY;

CREATE POLICY authoring_draft_workspace_isolation ON authoring_draft
  USING (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  )
  WITH CHECK (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  );

COMMENT ON TABLE authoring_draft IS
  'Private resumable authoring state isolated by PostgreSQL RLS.';
