# Atlas V2 verification — 2026-07-29

## Proven framework behavior

- One `KnowledgeEntity` kernel serves animal, plant, and electronic-component
  domains through declarative domain packs.
- Public search, taxonomy, and entries resolve current API data at request
  time.
- Safe `ViewDefinition` blocks render without generated executable code.
- The Agent DAG validates dependencies, retries bounded failures, persists
  checkpoints, resumes idempotently, and emits governance proposals only.
- Policy gates enforce evidence, confidence, risk classification, and distinct
  human reviewers.
- Accepted proposal operations create new immutable entity revisions. Stale
  `before` values and unknown citations block publication.
- Search is selected through one backend protocol. OpenSearch publication uses
  full versioned indexes and an atomic read-alias switch.
- Release manifests store before/after entity revision maps, search indexes,
  policy and Schema versions, and rollback state.
- Rollback restores the prior entity revision and rebuilds the corresponding
  search snapshot.

## Executed checks

- Python: Ruff passed; 155 pytest tests passed.
- TypeScript: all seven workspace packages passed strict checks.
- Builds: public Web, Admin, Desktop, generated API client, and Rust workspace
  passed.
- Playwright: eleven executable journeys passed in Microsoft Edge: six public
  discovery journeys, one Admin source-maintenance journey, one governed
  entry-revision journey, one conflict-aware bulk-review journey, and one
  runtime Domain Pack construction journey, plus one versioned quality
  maintenance journey. Public discovery, the Admin source
  center, entry workbench, review queue, Domain Pack builder, and quality
  workbench passed axe WCAG A/AA scans with no serious or critical violations.
- Browser/Web/API: `/ask` submitted a natural-language question only through
  the Web same-origin BFF, resolved the named entity, ranked one relevant cited
  section from its fixed revision, rendered the answer and evidence ledger,
  and passed axe WCAG A/AA without serious or critical violations.
- Answering/proxy: retrieval-only, proxy success, transport failure,
  out-of-whitelist evidence rejection and insufficient-evidence states passed.
  With the model feature explicitly enabled, the API called only the configured
  internal gateway URL and returned gateway/model/request provenance without
  exposing that URL. The exact answer replayed from Alembic-owned
  `knowledge_answer` storage.
- Browser: search and evidence-backed entry rendering passed.
- Browser: four-node Agent graph execution generated a persisted proposal.
- Browser/BFF: the Admin graph console loaded immutable Agent definitions and
  `graph-2.3.0`, then created a versioned `queued` schedule through
  `/api/backend`.
- API/audit: the proxied schedule request reached `/api/v1`, produced an
  `agent.schedule` success event, and the audit hash chain remained valid.
- Worker unit path: schedule transitions, checkpointed execution, governed
  proposal creation, Outbox confirmation, failed-delivery retention, and retry
  idempotency passed against SQLite.
- Runtime Domain Registry: three independently versioned packs merged shared
  definitions, resolved all references, and supplied the live API Schema
  Registry without Python Schema fixtures.
- Relationship kernel: typed relation validation, current PostgreSQL edge
  projection, outgoing/incoming traversal, and reverse labels passed.
- Browser: Admin `/schema` rendered Domain Pack fields and relation
  constraints; public `/relations/ne555` rendered the governed empty-graph
  state through the live API.
- Browser/BFF: `/schema/migrations` changed `attr-scientific-name` from text
  to decimal in a candidate document; the live analyzer classified it as
  `breaking`, counted two affected entities, required migration, marked it
  non-reversible, and performed no writes.
- API: a migratory enum change froze the Snow Leopard revision, created a
  high-risk Schema proposal, rejected pre-approval execution, required two
  distinct reviewers, atomically activated the new Schema and entity revision,
  then restored both through reverse replay.
- Browser/BFF: the migration workbench froze a two-entity compatible change,
  linked its governance proposal, displayed `planned`, and surfaced the
  expected “proposal is not accepted” response when execution was attempted.
- Browser: accepted Ginkgo proposal was staged and published through the
  release workbench.
- API: Ginkgo changed to revision
  `rev-ginkgo-atlas-live-search-001-52ce321822bc`; alias search returned that
  data version.
- Browser/API: rollback restored `rev-ginkgo-2026-07-28-001` and the original
  section body; search returned `atlas-2026.07.28`.
- Desktop: the Tauri reader loaded live spaces, taxonomy, search, entry
  evidence, and version metadata; SQLite upserted a newer offline revision and
  the browser fallback saved and removed an entry.
- Worker: the five-field source cron scheduler created one persisted
  acquisition job and Outbox event per due occurrence; repeat evaluation was
  idempotent and the dispatcher confirmed delivery.
- Admin/API: the operations dashboard replaced fixed presentation counters
  with a permission-protected persisted read model. Aggregate schedule and
  acquisition counts are computed in SQL; only recent display rows are
  bounded.
- Identity/API: authenticated principals received stable UUID workspace
  context. A collection in workspace A saved the exact Ginkgo revision,
  workspace B received `404`, and the runtime test data was removed afterward.
- Browser/Admin: the proposal detail rendered a live global Schema migration
  with its full JSON operation diff, citation identifier, dynamic impact,
  Agent run provenance, and policy gate. No entity-specific fallback content
  remained.
- Model proxy: MockTransport contract tests proved that optional-model Agents
  call only the configured proxy URL, inject credentials server-side, reject
  direct OpenAI/Azure provider hosts by default, preserve citations, and omit
  secrets from persisted provenance.
- API/Worker: content extraction and entity resolution recorded two model
  invocations with gateway/model/request IDs; the resulting value entered a
  governed proposal rather than published knowledge. No gateway configuration
  remained explicitly deterministic.
- Agent contracts: the `1.4.0` core Agent Pack, `graph-2.3.0` maintenance graph
  and zero-model `quality-maintenance-triage@graph-1.0.0` validate Draft
  2020-12 input/output Schemas at registration and node boundaries. Failed
  proxy attempts consume budget and persist only redacted error codes.
- Quality dispatch: `quality.maintenance.requested` pins an open task to its
  current entity revision and creates the graph schedule and
  `agent.graph.scheduled` event in one transaction. Retries reuse the same
  deterministic schedule; stale revisions are superseded without work. The
  triage Run consumes no model budget, has no proposal permission and records
  an evidence-required route without creating governed content.
- Source maintenance routing: a revision-pinned source Work Item selects one
  policy-allowed scoped source deterministically, creates a linked idempotent
  acquisition job, persists the immutable snapshot and completes only with
  repository-verified snapshot/job links. Scope ambiguity or absence blocks
  safely, and the route performs zero model calls and no encyclopedia write.
- Blocked-work recovery: only blocked Work Items can be requeued. The
  transition rechecks the fixed revision, clears the block reason, commits a
  fresh maintenance Outbox event and is exposed through the Admin same-origin
  BFF; duplicate or stale requeues fail closed.
- Translation/taxonomy execution: a lease holder can submit only typed,
  citation-backed operations against the pinned revision. Missing localized
  fields become a deterministic translation proposal; an allowed taxonomy
  node becomes a deterministic relation proposal. Proposal creation and Work
  completion commit atomically, response replay is idempotent, unknown nodes
  fail closed, lease tokens stay out of audit, and both medium-risk outputs
  enter one-approver human review rather than publication.
- Source/Agent/publication: a parser candidate pins the current entity revision
  and exact field value, embeds its immutable Citation, becomes one atomic
  Citation-plus-content proposal, passes policy review, and publishes a
  machine-generated value into a persisted immutable revision. Replaying the
  same candidate becomes a no-op; malformed self-evidencing citations and
  unknown manual citation references fail closed.
- Multi-instance governance: every persisted proposal carries a monotonic
  compare-and-swap version. An external Agent repository write performed after
  API startup becomes visible in the live queue and detail endpoint; a second
  writer holding the old version receives a deterministic concurrency conflict
  and cannot overwrite the newer policy state. Alembic head
  `0003_proposal_optimistic_lock` upgrades existing proposal documents to
  version 1 without replacing their governed content. Current Alembic head
  `0005_quality_maintenance` follows reproducible
  `0004_knowledge_answers` records with persistent quality assessments and
  maintenance tasks. Current Alembic head
  `0006_maintenance_work_queue` adds revision-pinned, leaseable Agent work.
- Automatic policy routing: proposal creation atomically emits
  `governance.proposal.created`; the dispatcher retains failed broker delivery
  and retries it, and the governance worker evaluates the current proposal
  idempotently. Safe low-risk proposals become accepted automatically,
  medium-risk proposals enter human review, and overlapping low-risk writes
  are forced into the conflict workflow. The manual policy-accept route cannot
  bypass the same active-conflict rule.
- Automatic release routing: the first transition to `accepted` atomically
  emits `governance.proposal.accepted`. The Worker batches compatible accepted
  entity proposals by policy version into a deterministic automatic release,
  records trigger and initiator provenance, and invokes the same
  `ReleaseOrchestrator` used by the manual API. Coverage proves that two
  proposals for different entities publish as one versioned release, move the
  search read index, create immutable revisions, and remain idempotent when the
  trigger is replayed. A simulated search-activation failure leaves the release
  in `publishing`; retry resumes it and applies each proposal only once.
- Post-release verification: publication completion atomically emits
  `release.published`. The ninth independent dispatcher topic drives a
  persistent verification attempt over frozen entity revisions, citation
  references, relationship targets, search readiness, the active index, and
  canonical-name discoverability. A temporary search outage remains retryable;
  a deterministic automatic-release regression records its failed checks and
  completes the shared rollback Saga exactly once. Manual failures preserve
  the human rollback decision, while a delayed event superseded by a newer
  active release cannot roll the newer version back.
- Production contract: startup tests reject development auth, SQLite,
  in-memory search, automatic Schema creation, localhost HTTP CORS, fixture
  content, and the fixture-backed hardware extension.
- Browser/Web: the Ginkgo entry exposed two immutable revisions. Its historical
  URL rendered a distinct body, Schema and data version with a clear warning;
  saving it pinned the historical revision in the personal workspace, and the
  verification collection was removed afterward.
- API: liveness and readiness are separate; database, search, Agent, Domain and
  Parser failures produce a component-level `503`. Current and fixed entity
  reads return revision ETags; fixed revisions are public immutable cache
  resources.
- API/Web: discovery browse and query share one backend contract with
  space/type/taxonomy/locale/source-tier/evidence filters, pagination and live
  facets. The category page selects one dynamic node at a time instead of
  rendering an unbounded catalogue.
- Web: the content-language selector persists a same-site preference and
  server-renders localized Domain Pack labels and entity content. The
  `atlas-2026.07.29` fixtures prove Chinese/English values without rewriting
  the prior immutable revisions.
- Admin/API: the Agent graph console consumes the generic authenticated SSE
  job stream and follows persisted schedule state through its terminal Run and
  proposal IDs.
- Worker/API: snapshot creation and extraction-batch creation now commit
  `source.snapshot.captured` and `source.extraction.completed` events in the
  same database transaction. Dispatcher coverage proves all four maintenance
  topics are independently retryable.
- Admin/API: the source center renders the persisted acquisition → snapshot →
  extraction → candidate → Agent schedule pipeline, exposes safe stage replay,
  and the browser journey proved every API request stayed behind the Admin
  same-origin BFF.
- Admin accessibility: the source center gained a document title and corrected
  43 low-contrast text nodes discovered by the executable axe check.
- Authoring/API: an existing immutable entity revision can be projected into
  the same Schema-driven draft contract used for creation. Revision proposals
  preserve exact before-values, reject stale base revisions and stable-identity
  changes, admit newly bound citations, publish a new immutable revision, and
  roll back to the pinned base revision.
- Authoring persistence: private create/revise drafts are isolated by
  Principal-derived workspace, use optimistic versions, reject stale updates,
  allow only editing/submitted/abandoned lifecycle transitions, and return the
  existing governed proposal on repeated submission.
- Browser/Admin: `/entries` loaded and pinned Ginkgo, updated the live safe-view
  preview, saved draft v1, restored it after a full page reload, advanced and
  submitted v3 through the same-origin BFF, and proved the public entity and
  revision remained unchanged before release.
- Relationship authoring: the workbench generated relation controls from the
  Plant Domain Pack, persisted the target and qualifier through a page reload,
  and submitted `/relationships` as a governed diff. API tests rejected forged
  target references and proved replacing an old cardinality-one edge does not
  count the outgoing edge being replaced.
- Multi-Agent governance: active proposals writing different values to equal
  or nested JSON Pointer paths are exposed as deterministic blocking
  conflicts. Single and bulk approvals return `409` until one competing
  proposal is rejected; bulk review preflights the complete set and persists
  one audited batch decision.
- Browser/Admin: `/reviews` created an isolated encyclopedia entity, surfaced
  a proposed candidate through the server-filtered paged queue, bulk-evaluated
  it, then surfaced two competing Agent revisions in the conflict ledger,
  selected retained values per JSON Pointer path, atomically marked both
  sources `superseded`, re-evaluated and approved the resulting merge proposal,
  and proved all browser API traffic stayed behind the Admin same-origin BFF.
  API coverage also proves duplicate or mixed-state evaluation batches fail
  complete preflight without transitioning an eligible neighbor.
- Browser/Admin: the isolated entity release also opened `/releases`, invoked
  the permission-protected verification endpoint through `/api/backend`,
  rendered all persisted check evidence and reached `passed`. The release
  workbench and its new verification panel passed axe WCAG A/AA with no
  serious or critical violations. The same expanded catalogue exposed and
  corrected a previously unreachable public search-pagination contrast issue.
- Browser/Admin: `/schema/domain-packs` created a new oceanography space,
  taxonomy, entity type, attribute, typed cross-domain relation to the
  published Animal type, qualifier Schema and safe view without code
  generation; validation produced an immutable pack draft and high-risk Schema
  proposal. After dual review the same journey publishes it, verifies the
  exact rollback snapshot, inspects the seven-document version diff and runtime
  dependency analysis through the same-origin BFF, then restores the
  pre-publication state with optimistic concurrency, Outbox and audit comment.
  API coverage additionally proves a live entity blocks removal of both its
  entity type and taxonomy until the entity is deactivated. The journey also
  corrected 11 inherited contrast failures and made the generated manifest
  keyboard-focusable.
- Domain quality kernel: animals, plants and electronic components load
  independently versioned declarative quality profiles. The registry rejects
  unknown entity types, out-of-type required attributes and duplicate active
  profiles. Deterministic evaluation covers required attributes, locales,
  citations, sections, uncited content, taxonomy and freshness without
  executing Pack code or calling a model.
- Persistence/API/Worker: `0005_quality_maintenance` stores immutable
  assessment snapshots and deterministic task IDs. Repeated scans do not
  duplicate tasks or Outbox requests; a new entity revision supersedes obsolete
  work. Read and execution permissions are separate, selected missing entities
  are isolated as failures, and the retryable Worker actor plus periodic
  scheduler consume the same service. Maintenance Outbox events atomically pin
  the task and schedule to the current revision; stale tasks create no work,
  retries remain idempotent, and invalid events stay pending for repair.
- Multi-Agent work queue: completed safety triage atomically writes
  `maintenance_work_item` and `maintenance.work.requested`. Broker activation
  exposes only current-revision work; authenticated Agents claim it with a
  bounded lease, same-Agent retries reuse the token, competing Agents fail
  closed, expired leases are reclaimable, and revision changes supersede both
  work and task. Completion rejects missing evidence, verifies persisted source
  snapshots and enforces route-compatible governed outputs; blocking requires
  a durable reason. Queue reads and audit events never expose lease tokens.
- Browser/Admin: `/quality` scanned the expanded local catalogue, displayed
  exact revision, profile version, issue path, penalty and bounded repair
  action for every active task, paginated 28 assessments and 81 tasks without
  horizontal overflow, and rendered a real `ready` source-acquisition Work
  Item with its fixed Ginkgo revision, required capabilities and empty lease.
  All browser API traffic stayed on the Admin same-origin BFF/auth boundary.
  The journey passed axe WCAG A/AA with no serious or critical violations
  after correcting table semantics and low-contrast metadata.

## External image channel

Screens 01 and 02 exist. Screen 02 was generated through Codex's built-in
Image 2 route and archived at 1672×941. After the 149-test governed quality
dispatch regression, screen 03 was retried as a reference-free generation.
The built-in generation route again returned an image-backend network error;
earlier edit and generation attempts had the same condition. Prompts for
screens 03–08 remain archived; no API-key, project proxy, CLI, or
alternate-model fallback was substituted.

## Environment boundary

The current machine has no Docker CLI. PostgreSQL, Redis, OpenSearch, MinIO,
and Keycloak Compose integration was therefore not represented as a live pass.
In browser verification the durable schedule intentionally remained `queued`
because no Redis worker was running.
