CREATE TABLE IF NOT EXISTS knowledge_space (
  id text PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  document jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('draft', 'published', 'archived'))
);

CREATE TABLE IF NOT EXISTS taxonomy_node (
  id text PRIMARY KEY,
  space_id text NOT NULL REFERENCES knowledge_space(id),
  slug text NOT NULL,
  document jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS taxonomy_node_space_id_idx
  ON taxonomy_node (space_id);

CREATE TABLE IF NOT EXISTS schema_document (
  id text NOT NULL,
  schema_version text NOT NULL,
  kind text NOT NULL CHECK (
    kind IN (
      'entity-type',
      'attribute-definition',
      'relationship-type',
      'view-definition',
      'agent-definition',
      'agent-graph'
    )
  ),
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, schema_version)
);

CREATE TABLE IF NOT EXISTS knowledge_entity (
  id text PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  entity_type_id text NOT NULL,
  publication_status text NOT NULL,
  current_revision_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_revision (
  id text PRIMARY KEY,
  entity_id text NOT NULL REFERENCES knowledge_entity(id),
  data_version text NOT NULL,
  schema_version text NOT NULL,
  policy_version text NOT NULL,
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE knowledge_entity
  ADD CONSTRAINT knowledge_entity_current_revision_fk
  FOREIGN KEY (current_revision_id) REFERENCES entity_revision(id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS relation_edge (
  id text PRIMARY KEY,
  relationship_type_id text NOT NULL,
  source_entity_id text NOT NULL REFERENCES knowledge_entity(id),
  target_entity_id text NOT NULL REFERENCES knowledge_entity(id),
  qualifiers jsonb NOT NULL DEFAULT '{}',
  confidence numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  revision_id text NOT NULL REFERENCES entity_revision(id)
);

CREATE TABLE IF NOT EXISTS agent_run (
  id text PRIMARY KEY,
  role text NOT NULL,
  status text NOT NULL,
  model_version text NOT NULL,
  prompt_version text NOT NULL,
  policy_version text NOT NULL,
  provenance jsonb NOT NULL,
  started_at timestamptz,
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS change_proposal (
  id text PRIMARY KEY,
  agent_run_id text NOT NULL REFERENCES agent_run(id),
  entity_id text REFERENCES knowledge_entity(id),
  proposal_type text NOT NULL,
  operations jsonb NOT NULL,
  risk text NOT NULL,
  status text NOT NULL,
  impact jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE change_proposal IS
  'Agents write proposals; published entity revisions are created only after policy and review gates.';

CREATE TABLE IF NOT EXISTS governed_proposal (
  id text PRIMARY KEY,
  entity_id text,
  status text NOT NULL,
  risk text NOT NULL,
  document jsonb NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_graph_run (
  id text PRIMARY KEY,
  graph_id text NOT NULL,
  status text NOT NULL,
  document jsonb NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS release_manifest (
  id text PRIMARY KEY,
  data_version text NOT NULL UNIQUE,
  status text NOT NULL,
  previous_release_id text NOT NULL,
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_outbox (
  id text PRIMARY KEY,
  topic text NOT NULL,
  aggregate_id text NOT NULL,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL,
  published_at timestamptz,
  error text
);
