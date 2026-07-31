# Domain boundaries and data flow

## Core aggregates

- Spaces and taxonomy: independent knowledge spaces and poly-hierarchical
  category nodes.
- Schema: domain packs, entity types, attributes, relationship types, and safe
  view blocks.
- Knowledge: canonical entity, localized names, aliases, structured claims,
  sections, citations, typed relationships, and immutable revisions.
- Evidence: governed source definition, immutable source snapshot, citation,
  license, confidence, and review.
- Agent maintenance: Agent definition, graph template, budget, schedule,
  checkpoint, usage, and proposal.
- Governance: proposal, policy evaluation, distinct human reviews, release
  manifest, outbox event, audit event, and rollback.
- Extensions: hardware devices, compatibility rules, projects, and lifecycle
  records remain optional domain modules.

Every entity revision remains addressable through
`/api/v1/entities/{slug}/revisions/{revisionId}` and the public Web exposes a
matching fixed URL. Rollback changes only the current pointer; it does not
erase the newer historical document.

## Publication flow

1. A governed source snapshot creates staging claims and provenance records.
2. Normalization proposes entity and field changes.
3. Review accepts, partially accepts, rejects, or requests evidence.
4. A release batch freezes catalog, relation, and rule changes.
5. Golden tests and impact analysis run against the frozen batch.
6. A transaction publishes the version and writes outbox events.
7. Workers build a new OpenSearch index and atomically swap the read alias.
8. Domain-specific projections are re-evaluated without overwriting historical
   reports.

## Trust boundary

The desktop scanner can collect raw hardware identifiers locally. Only approved
fields cross into cloud synchronization. Serial numbers, MAC addresses, and
stable device IDs are excluded or irreversibly transformed before upload.
