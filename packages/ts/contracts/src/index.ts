export type LocaleCode = string;
export type EntityId = string;
export type SchemaVersion = string;

export interface LocalizedText {
  locale: LocaleCode;
  value: string;
  machineGenerated?: boolean;
}

export interface KnowledgeSpace {
  id: string;
  slug: string;
  name: LocalizedText[];
  description: LocalizedText[];
  rootTaxonomyNodeIds: string[];
  iconKey: string;
  status: "draft" | "published" | "archived";
}

export interface TaxonomyNode {
  id: string;
  spaceId: string;
  slug: string;
  name: LocalizedText[];
  parentIds: string[];
  childCount: number;
  entityCount: number;
  pathKeys: string[];
}

export type AttributeDataType =
  | "text"
  | "rich-text"
  | "integer"
  | "decimal"
  | "boolean"
  | "date"
  | "date-range"
  | "measurement"
  | "enum"
  | "entity-ref"
  | "media-ref"
  | "geo"
  | "json";

export interface AttributeDefinition {
  id: string;
  key: string;
  name: LocalizedText[];
  dataType: AttributeDataType;
  cardinality: "one" | "many";
  required: boolean;
  unitFamily?: string;
  enumValues?: string[];
  validation?: Record<string, unknown>;
  schemaVersion: SchemaVersion;
}

export interface EntityType {
  id: string;
  spaceId: string;
  key: string;
  name: LocalizedText[];
  description: LocalizedText[];
  allowedTaxonomyNodeIds: string[];
  attributeDefinitionIds: string[];
  allowedRelationshipTypeIds: string[];
  defaultViewDefinitionId: string;
  schemaVersion: SchemaVersion;
}

export interface EntityRef {
  id: EntityId;
  slug: string;
  typeId: string;
  canonicalName: string;
}

export interface Citation {
  id: string;
  sourceId: string;
  sourceTitle: string;
  sourceUrl?: string;
  sourceTier: "primary" | "authoritative" | "secondary" | "community";
  retrievedAt: string;
  locator?: string;
  quoteHash?: string;
}

export interface ClaimValue {
  id: string;
  attributeDefinitionId: string;
  originalValue: unknown;
  normalizedValue?: unknown;
  displayValue: LocalizedText[];
  unit?: string;
  validFrom?: string;
  validTo?: string;
  confidence: number;
  citationIds: string[];
  revisionId: string;
}

export interface ContentSection {
  id: string;
  key: string;
  heading: LocalizedText[];
  body: LocalizedText[];
  citationIds: string[];
  order: number;
}

export interface RelationshipType {
  id: string;
  key: string;
  name: LocalizedText[];
  inverseName: LocalizedText[];
  directed: boolean;
  sourceEntityTypeIds: string[];
  targetEntityTypeIds: string[];
  sourceCardinality: "one" | "many";
  targetCardinality: "one" | "many";
  qualifierSchema: Record<string, unknown>;
  evidenceRequired: boolean;
  schemaVersion: SchemaVersion;
}

export interface Relationship {
  id: string;
  typeId: string;
  source: EntityRef;
  target: EntityRef;
  qualifiers: Record<string, unknown>;
  confidence: number;
  citationIds: string[];
  revisionId: string;
}

export interface RelationshipTraversalItem {
  relationship: Relationship;
  relationshipType: RelationshipType;
  direction: "outgoing" | "incoming";
  neighbor: EntityRef;
}

export type ViewBlockType =
  | "hero"
  | "summary"
  | "classification"
  | "attribute-table"
  | "relationship-list"
  | "timeline"
  | "map"
  | "media-gallery"
  | "citations";

export interface ViewBlock {
  id: string;
  type: ViewBlockType;
  title?: LocalizedText[];
  config: Record<string, unknown>;
  visibleWhen?: Record<string, unknown>;
}

export interface ViewDefinition {
  id: string;
  entityTypeId: string;
  schemaVersion: SchemaVersion;
  blocks: ViewBlock[];
}

export interface RevisionContext {
  revisionId: string;
  dataVersion: string;
  schemaVersion: string;
  policyVersion: string;
  createdAt: string;
}

export interface KnowledgeEntity {
  ref: EntityRef;
  names: LocalizedText[];
  aliases: LocalizedText[];
  description: LocalizedText[];
  taxonomyNodeIds: string[];
  claims: ClaimValue[];
  sections: ContentSection[];
  relationships: Relationship[];
  citations: Citation[];
  revision: RevisionContext;
  publicationStatus: "draft" | "review" | "published" | "archived";
}

export interface AnswerEvidence {
  id: string;
  entity: EntityRef;
  revisionId: string;
  kind: "section" | "claim";
  label: string;
  text: string;
  citationIds: string[];
  confidence: number;
}

export interface AnswerModelProvenance {
  gatewayId: string;
  model: string;
  requestId: string;
  status: "succeeded" | "failed";
  errorCode?: string;
  inputTokens: number;
  outputTokens: number;
}

export interface KnowledgeAnswer {
  id: string;
  question: string;
  locale: string;
  status: "answered" | "insufficient-evidence";
  mode: "retrieval-synthesis" | "model-proxy" | "retrieval-fallback";
  answer: string;
  entities: EntityRef[];
  evidence: AnswerEvidence[];
  citations: Citation[];
  dataVersions: string[];
  modelProvenance?: AnswerModelProvenance;
  fallbackReason?: string;
  createdAt: string;
}

export interface DraftSection {
  key: string;
  heading: LocalizedText[];
  body: LocalizedText[];
  citationIds: string[];
}

export interface EntityDraft {
  id: EntityId;
  slug: string;
  typeId: string;
  names: LocalizedText[];
  aliases: LocalizedText[];
  description: LocalizedText[];
  taxonomyNodeIds: string[];
  attributeValues: Record<string, unknown>;
  sections: DraftSection[];
  citations: Citation[];
}

export interface DraftIssue {
  severity: "error" | "warning";
  code: string;
  path: string;
  message: string;
}

export interface DraftValidation {
  valid: boolean;
  entityTypeId: string;
  schemaVersion: SchemaVersion;
  issues: DraftIssue[];
}

export interface SchemaRegistry {
  entityTypes: EntityType[];
  attributeDefinitions: AttributeDefinition[];
  relationshipTypes: RelationshipType[];
  viewDefinitions: ViewDefinition[];
}

export interface SourceDefinition {
  id: string;
  version: string;
  name: string;
  kind: "website" | "api" | "feed" | "file" | "dataset";
  baseUrl: string;
  allowedHosts: string[];
  trustTier: "primary" | "authoritative" | "secondary" | "community";
  licenseId: string;
  licenseStatus: "allowed" | "review-required" | "blocked";
  robotsPolicy: "respect" | "explicit-api" | "not-applicable";
  robotsStatus: "allowed" | "review-required" | "blocked";
  allowedMediaTypes: string[];
  maxBytes: number;
  locales: string[];
  entityTypeIds: string[];
  taxonomyNodeIds: string[];
  schedule?: string;
  status: "active" | "paused" | "blocked";
  createdAt: string;
}

export interface SourcePolicyDecision {
  allowed: boolean;
  sourceId: string;
  sourceVersion: string;
  blockers: string[];
  warnings: string[];
}

export interface SourceRegistryRecord {
  source: SourceDefinition;
  policy: SourcePolicyDecision;
}

export interface SourceSnapshot {
  id: string;
  sourceId: string;
  sourceVersion: string;
  url: string;
  contentSha256: string;
  storageKey: string;
  mediaType: string;
  byteSize: number;
  httpStatus: number;
  etag?: string;
  lastModified?: string;
  licenseId: string;
  captureStatus: "captured" | "unchanged" | "rejected";
  retrievedAt: string;
}

export type Permission =
  | "maintenance.read"
  | "agent.run"
  | "proposal.create"
  | "proposal.evaluate"
  | "proposal.review"
  | "release.stage"
  | "release.publish"
  | "release.rollback"
  | "source.register"
  | "source.read"
  | "audit.read";

export interface Principal {
  subject: string;
  displayName: string;
  email?: string;
  workspaceId?: string;
  roles: string[];
  authenticationMethod: "development" | "oidc" | "system";
}

export interface SavedCollection {
  id: string;
  workspaceId: string;
  name: string;
  description: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface SavedCollectionItem {
  collectionId: string;
  workspaceId: string;
  entityId: string;
  entityRef: EntityRef;
  entityRevisionId: string;
  dataVersion: string;
  note: string;
  tags: string[];
  savedBy: string;
  savedAt: string;
}

export interface AuditEvent {
  id: string;
  occurredAt: string;
  actor: Principal;
  action: string;
  resourceType: string;
  resourceId: string;
  outcome: "success" | "denied" | "failed";
  requestId: string;
  metadata: Record<string, unknown>;
  previousHash?: string;
  eventHash: string;
}

export type AgentRole =
  | "source-monitor"
  | "acquisition"
  | "extraction"
  | "entity-resolution"
  | "normalization"
  | "translation"
  | "evidence-verification"
  | "conflict-analysis"
  | "quality-assurance";

export interface AgentRun {
  id: string;
  role: AgentRole;
  status: "queued" | "running" | "waiting-review" | "completed" | "failed";
  modelVersion: string;
  promptVersion: string;
  policyVersion: string;
  inputSourceIds: string[];
  proposalIds: string[];
  startedAt?: string;
  completedAt?: string;
}

export interface ChangeOperation {
  operation: "add" | "replace" | "remove" | "merge";
  path: string;
  before?: unknown;
  after?: unknown;
  citationIds: string[];
  confidence: number;
  machineGenerated?: boolean;
}

export interface AgentProposal {
  id: string;
  entityId?: EntityId;
  proposalType: "content" | "relation" | "schema" | "translation" | "merge";
  operations: ChangeOperation[];
  risk: "low" | "medium" | "high" | "critical";
  status:
    | "proposed"
    | "policy-approved"
    | "policy-blocked"
    | "human-review"
    | "accepted"
    | "rejected"
    | "superseded"
    | "released";
  agentRunId: string;
  impact: Record<string, number>;
}

export * from "./extensions/hardware";
