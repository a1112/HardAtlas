CREATE TABLE IF NOT EXISTS saved_collection (
  id text PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspace(id),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (id, workspace_id)
);

CREATE TABLE IF NOT EXISTS saved_collection_item (
  collection_id text NOT NULL,
  entity_id text NOT NULL REFERENCES knowledge_entity(id),
  workspace_id uuid NOT NULL REFERENCES workspace(id),
  document jsonb NOT NULL,
  saved_at timestamptz NOT NULL,
  PRIMARY KEY (collection_id, entity_id),
  FOREIGN KEY (collection_id, workspace_id)
    REFERENCES saved_collection(id, workspace_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS saved_collection_workspace_updated_idx
  ON saved_collection (workspace_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS saved_collection_item_workspace_saved_idx
  ON saved_collection_item (workspace_id, saved_at DESC);

ALTER TABLE saved_collection ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_collection FORCE ROW LEVEL SECURITY;
ALTER TABLE saved_collection_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_collection_item FORCE ROW LEVEL SECURITY;

CREATE POLICY saved_collection_workspace_isolation ON saved_collection
  USING (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  )
  WITH CHECK (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  );

CREATE POLICY saved_collection_item_workspace_isolation ON saved_collection_item
  USING (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  )
  WITH CHECK (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  );

COMMENT ON TABLE saved_collection IS
  'Private user or organization collections isolated by PostgreSQL RLS.';

COMMENT ON TABLE saved_collection_item IS
  'Version-pinned saved encyclopedia entries isolated by PostgreSQL RLS.';
