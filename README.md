# Atlas — agent-native universal encyclopedia

Atlas is a schema-driven encyclopedia framework designed to grow across
unrelated knowledge domains without application rewrites. Search and dynamic
taxonomy are the public entry points; structured evidence, revisions, and
multi-agent maintenance form the production system.

The first reference domains deliberately differ:

- animals: snow leopard;
- plants: ginkgo;
- electronics: NE555 timer.

Computer hardware scanning and compatibility remain an optional domain
extension, not a dependency of the encyclopedia kernel.

## Repository map

- `apps/web`: public search, taxonomy, entry rendering, and personal collections.
- `apps/admin`: agent operations, proposals, schema governance, and releases.
- `apps/desktop`: offline encyclopedia reader with optional native extensions.
- `apps/api`: FastAPI knowledge, schema, search, maintenance, and extension APIs.
- `apps/worker`: source monitoring, extraction, resolution, validation, and release jobs.
- `domain-packs`: declarative entity types, attributes, relations, views, and policies.
- `packages/ts`: shared contracts, UI, design tokens, and generated API client.
- `packages/py`: domain kernel, persistence, rules, ingestion, and AI boundaries.
- `crates`: optional trusted native extensions, including the hardware scanner.
- `docs`: product vision, archived requirements, architecture, and design.

## Local quick start

```bash
corepack pnpm install
uv sync --all-packages
uv run alembic -c packages/py/data/alembic.ini upgrade head
corepack pnpm dev:web
corepack pnpm dev:admin
uv run uvicorn hardatlas_api.app:app --reload
```

To execute durable agent schedules and automatic source acquisition, run the
Dramatiq worker, transactional-outbox poller, and source cron scheduler against
the same database:

```bash
uv run dramatiq hardatlas_worker.tasks --processes 1 --threads 4
uv run --package hardatlas-worker hardatlas-dispatcher
uv run --package hardatlas-worker hardatlas-scheduler
```

The dispatcher independently polls `source.acquisition.requested`,
`source.snapshot.captured`, `source.extraction.completed`,
`quality.maintenance.requested`, `agent.graph.scheduled`,
`maintenance.work.requested`, `governance.proposal.created`,
`governance.proposal.accepted`, and `release.published`; a temporary failure in
one stage does not stop the others. The scheduler converts each due five-field
source cron occurrence into one idempotent persisted acquisition job and its
Outbox event. It also evaluates published revisions against active Domain Pack
quality profiles and reconciles the persistent Agent maintenance queue.
Database bootstrap and upgrades are owned by Alembic; production API and worker
processes never create tables opportunistically. See
[`docs/architecture/database-migrations.md`](docs/architecture/database-migrations.md).

The Tauri desktop client is a functional encyclopedia reader rather than a
hardware dashboard. It loads spaces and taxonomy dynamically, searches the
public knowledge API, renders structured evidence-backed entries, and stores
versioned offline snapshots in local SQLite. See
[`docs/architecture/desktop-offline-reader.md`](docs/architecture/desktop-offline-reader.md).

Verification:

```bash
corepack pnpm -r --if-present check
corepack pnpm -r --if-present build
corepack pnpm test:e2e
uv run ruff check .
uv run pytest
cargo check --workspace
```

The Playwright suite starts or reuses the API, Web, and Admin surfaces and
validates dynamic taxonomy navigation, backend discovery filters, persisted
language selection, immutable revisions, the governed source-maintenance
pipeline, Admin BFF isolation, and serious/critical WCAG A/AA findings.

Set `HARDATLAS_API_URL=http://localhost:8000` for the public Web server. When
an API URL is configured, API failures are surfaced instead of silently
showing fixture data. Development fixture fallback can only be re-enabled
explicitly with `HARDATLAS_ALLOW_FIXTURE_FALLBACK=true`; production without an
API URL fails visibly and never substitutes local catalogue content.

The default API uses the deterministic in-memory lexical backend. Set
`HARDATLAS_SEARCH_BACKEND=opensearch` to use the versioned OpenSearch index and
`atlas-knowledge-read` alias. Publishing then builds a complete index before
switching the read alias; rollback creates a restored index from the recorded
entity revision map.

Public discovery uses the same backend contract for query and browse. Space,
entity type, taxonomy, content language, source tier and minimum-evidence
filters are executed by the backend and returned with facets and pagination.
The category browser selects one domain and taxonomy node at a time, so adding
large Domain Packs does not require rendering the entire tree or catalogue.
The Web language preference is stored in a same-site cookie and selects
localized entity, domain, taxonomy and section values with deterministic
language fallback.

Natural-language questions are handled by the evidence-grounded `/ask`
experience. The browser posts only to the same-origin Web BFF; the API resolves
published revisions, ranks cited sections and claims, and persists the exact
answer for replay. Optional synthesis is disabled by default and can only use
the configured server-side model gateway. Unknown evidence IDs or gateway
failure visibly fall back to the deterministic cited answer. See
[`docs/architecture/evidence-grounded-answering.md`](docs/architecture/evidence-grounded-answering.md).

The admin `/schema` workbench is generated from the live Schema Registry. It
validates drafts on the server, creates governed proposals, and supports
publishing or rolling back brand-new entity revisions. See
[`docs/architecture/dynamic-authoring.md`](docs/architecture/dynamic-authoring.md).
The `/schema/domain-packs` constructor can add a complete knowledge space,
taxonomy root, entity type, attributes, and default view at runtime. Validation
rejects unresolved references and Schema overwrites; publication commits the
whole pack and its Outbox event atomically. See
[`docs/architecture/runtime-domain-packs.md`](docs/architecture/runtime-domain-packs.md).
Each Pack may also declare a versioned quality profile. Admin `/quality`
evaluates current revisions, exposes exact missing fields, locales, evidence
and freshness rules, and persists deterministic Agent-ready tasks; see
[`docs/architecture/knowledge-quality-maintenance.md`](docs/architecture/knowledge-quality-maintenance.md).
Those tasks enter a version-pinned, zero-model safety triage graph through a
durable Outbox chain. The triage graph cannot create content proposals and
only routes work toward source acquisition, translation evidence or taxonomy
review.
Completed triage Runs become durable, revision-pinned Work Items that Agents
claim through bounded leases. Lease tokens are never exposed by queue reads,
and stale revisions supersede both task and work before execution; see
[`docs/architecture/maintenance-work-queue.md`](docs/architecture/maintenance-work-queue.md).
Agents may complete work only with repository-verified citation/source
snapshots and route-compatible governed outputs, or block it with an auditable
reason. Source-route Work Items already run through a deterministic selector:
versioned source scope, policy and trust rank must produce one winner before an
idempotent acquisition job is created. Its immutable snapshot closes the
evidence task but never edits encyclopedia content directly.
Translation and taxonomy Agents submit their results through route-specific
API contracts. The server verifies the lease, fixed revision, existing
citations, missing locale or allowed taxonomy node, then atomically creates a
medium-risk governed proposal and completes the Work Item. Those proposals
still pass policy evaluation and human review before any release.
Runtime Schema comes from the comma-separated `HARDATLAS_DOMAIN_PACK_PATHS`;
the core pack owns knowledge spaces and root taxonomy while domain packs own
types and views. Cross-pack conflicts or unresolved references fail startup.
Set `HARDATLAS_SEED_FIXTURE_CONTENT=false` outside local demonstrations;
production startup rejects fixture seeding, development authentication,
SQLite, in-memory search, opportunistic table creation, localhost CORS, and the
fixture-backed hardware endpoint. Typed forward/inverse relations and the
current PostgreSQL graph projection are described in
[`docs/architecture/relationship-registry.md`](docs/architecture/relationship-registry.md).
Governed Schema impact analysis, deterministic migration, activation pointers,
and reverse replay are described in
[`docs/architecture/schema-migrations.md`](docs/architecture/schema-migrations.md).

Agent source acquisition is separately governed by versioned license, robots,
host, media type, and size policies. Raw bytes are addressed by SHA-256 in
S3/MinIO and their metadata is immutable. See
[`docs/architecture/source-acquisition.md`](docs/architecture/source-acquisition.md).
Immutable snapshots are converted into provenance-bound candidates by
versioned, declarative parser packs; see
[`docs/architecture/source-extraction.md`](docs/architecture/source-extraction.md).

Privileged maintenance uses OIDC roles, a secure-cookie Admin BFF, and a
tamper-evident audit chain. See
[`docs/architecture/identity-and-audit.md`](docs/architecture/identity-and-audit.md).
Authenticated readers can create version-pinned private collections. Explicit
repository scoping and forced PostgreSQL RLS isolate each workspace; see
[`docs/architecture/personal-collections-and-rls.md`](docs/architecture/personal-collections-and-rls.md).
The Web reader exposes this at `/collections` through a separate OIDC/PKCE BFF;
entry pages save the exact revision shown on screen without exposing tokens to
client JavaScript.

Agent definitions and graph templates are immutable declarative documents.
Schedules pin graph, Agent and budget versions, flow through PostgreSQL Outbox
and Redis/Dramatiq, and may only create governed proposals. See
[`docs/architecture/agent-registry-and-scheduling.md`](docs/architecture/agent-registry-and-scheduling.md).
Policy-accepted entity proposals are grouped into deterministic automatic
releases and published through the same orchestration boundary as manual
releases. Production automatic publication requires the versioned OpenSearch
backend; an in-process search index is rejected across Worker/API process
boundaries. Every completed publication emits a transactional
`release.published` event. The verification Worker checks frozen revisions,
citation and relationship integrity, the active read alias, and exact-name
discoverability. Failed automatic releases are rolled back through the same
retryable Release orchestrator when `HARDATLAS_AUTO_ROLLBACK_ENABLED=true`;
manual releases remain a human rollback decision. See
[`docs/architecture/search-publication.md`](docs/architecture/search-publication.md).
Optional-model Agents use a single server-side proxy boundary with no default
provider URL. Direct provider hosts are denied by default, and successful
calls persist gateway/model/request provenance; see
[`docs/architecture/model-gateway-and-proxy.md`](docs/architecture/model-gateway-and-proxy.md).
The Admin operations home derives its counters and queues from persisted runtime
state through a permission-protected read model; see
[`docs/architecture/operations-observability.md`](docs/architecture/operations-observability.md).
Concurrent Agent proposals are checked for overlapping incompatible paths, and
reviewers can safely process preflighted batches from Admin `/reviews`; see
[`docs/architecture/proposal-conflicts-and-bulk-review.md`](docs/architecture/proposal-conflicts-and-bulk-review.md).
The Admin `/entries` workbench creates new entities or projects an immutable
entity revision back into the same Schema-driven draft contract. Private
workspace drafts survive page reloads, reject stale-version overwrites, and
generate attributes and typed relationships directly from Domain Packs. They
produce evidence-backed diffs only; publication remains a separate governed
release action. See
[`docs/architecture/dynamic-authoring.md`](docs/architecture/dynamic-authoring.md).

The original hardware functional design remains byte-identical under
`docs/product/requirements/v1.0` as historical input. Atlas V2 product intent
is defined in `docs/product/vision-v2.md`.

## Fast verification pipeline

Run end-to-end checks with a single command:

```bash
corepack pnpm verify
```

This runs archive integrity, type/tests checks, production build, pytest,
Rust workspace check, Playwright suite, and image-asset verification.
