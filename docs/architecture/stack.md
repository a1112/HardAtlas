# Technical stack

## Architecture shape

P0 is a modular monolith with independently deployable user interfaces. Domain
packages own the business rules; HTTP, jobs, search, and persistence are
adapters. Splitting a module into a service requires measured scaling,
deployment, or ownership pressure rather than speculative boundaries.

## Selected technologies

| Concern          | Selection                                         | Reason                                                                |
| ---------------- | ------------------------------------------------- | --------------------------------------------------------------------- |
| Monorepo         | pnpm + Turborepo, uv workspaces, Cargo workspaces | Native tooling for each language with one repository and one CI graph |
| Public web/admin | Next.js App Router, React, TypeScript             | SSR for public knowledge and a shared component model                 |
| Desktop          | Tauri 2, React, Rust, SQLite                      | Small trusted native boundary and local-first device state            |
| API              | FastAPI, Pydantic, SQLAlchemy, Alembic            | Typed OpenAPI source and strong Python data/AI ecosystem              |
| System of record | PostgreSQL 18                                     | Transactions, versioned relational graph, JSONB extensions and RLS    |
| Search           | OpenSearch                                        | Fast autocomplete, faceting, aliases and optional hybrid retrieval    |
| Jobs/cache       | Dramatiq + Redis, PostgreSQL outbox               | Retryable asynchronous work without making Redis the source of truth  |
| Objects          | S3-compatible storage; MinIO locally              | Evidence documents, imports and image derivatives                     |
| Auth             | OIDC/OAuth2; Cookie for Web, PKCE for desktop     | Replaceable provider and appropriate client security                  |
| Telemetry        | OpenTelemetry                                     | Cross-request, job, release, rule and AI trace correlation            |

## Explicit exclusions

- No GraphQL in P0. REST/OpenAPI is the stable public contract.
- No microservice-per-domain topology.
- No Neo4j in P0.
- No executable user-authored compatibility code.
- No AI dependency in search, structured comparison, or deterministic
  compatibility.
