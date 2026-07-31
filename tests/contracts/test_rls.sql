BEGIN;

INSERT INTO workspace (id, name)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'workspace-a'),
  ('00000000-0000-0000-0000-000000000002', 'workspace-b');

SET LOCAL app.workspace_id = '00000000-0000-0000-0000-000000000001';

INSERT INTO device_snapshot (
  id,
  workspace_id,
  captured_at,
  approved_payload,
  data_version
) VALUES (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  now(),
  '{"fixture": true}',
  'fixture-2026.07.28'
);

INSERT INTO saved_collection (
  id,
  workspace_id,
  name,
  document,
  created_at,
  updated_at
) VALUES (
  'collection-workspace-a',
  '00000000-0000-0000-0000-000000000001',
  'workspace-a collection',
  '{"fixture": true}',
  now(),
  now()
);

INSERT INTO authoring_draft (
  id,
  workspace_id,
  created_by,
  mode,
  status,
  entity_id,
  version,
  document,
  created_at,
  updated_at
) VALUES (
  'draft-workspace-a',
  '00000000-0000-0000-0000-000000000001',
  'author-a',
  'create',
  'editing',
  'entity-private-draft',
  1,
  '{"fixture": true}',
  now(),
  now()
);

-- Run this contract as a non-owner application role. It must return zero.
SET LOCAL app.workspace_id = '00000000-0000-0000-0000-000000000002';
SELECT count(*) AS forbidden_rows FROM device_snapshot
WHERE id = '10000000-0000-0000-0000-000000000001';

SELECT count(*) AS forbidden_collections FROM saved_collection
WHERE id = 'collection-workspace-a';

SELECT count(*) AS forbidden_authoring_drafts FROM authoring_draft
WHERE id = 'draft-workspace-a';

ROLLBACK;
