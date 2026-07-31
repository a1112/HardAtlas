CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS workspace (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL
);

CREATE TABLE IF NOT EXISTS device_snapshot (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspace(id),
  captured_at timestamptz NOT NULL,
  approved_payload jsonb NOT NULL,
  data_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  topic text NOT NULL,
  aggregate_id text NOT NULL,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);

ALTER TABLE device_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_snapshot FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_device_isolation ON device_snapshot
  USING (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  )
  WITH CHECK (
    workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
  );

COMMENT ON POLICY workspace_device_isolation ON device_snapshot IS
  'Private snapshots are visible and writable only in the transaction workspace context.';
