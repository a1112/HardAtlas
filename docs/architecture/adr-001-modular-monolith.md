# ADR-001: Start with a modular monolith

- Status: Accepted
- Date: 2026-07-28

## Decision

Keep catalog, evidence, device, project, lifecycle, compatibility, and release
logic as Python packages behind one API and worker deployment. Public Web,
admin, and desktop remain separate applications. PostgreSQL is the single
system of record; OpenSearch is a rebuildable read model.

## Consequences

- Transactions can preserve cross-domain data and rule versions.
- Tests can run without a distributed deployment.
- Module boundaries must be enforced in imports and schemas.
- A module may be extracted only after operational evidence justifies it.
