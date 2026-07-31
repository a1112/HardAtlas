# Operations observability

The Admin home page is an operational view over persisted Atlas state. It must
not contain presentation-only counters or pretend that fixture data is a live
production signal.

Runtime orchestration must use `GET /api/v1/health` for liveness and
`GET /api/v1/ready` for readiness. The latter returns `503` if PostgreSQL,
the active search alias, or any required declarative registry is unavailable,
so a responsive but incomplete API instance is removed from service.

## Read model

`GET /api/v1/operations/summary` returns a compact read model for the Admin
dashboard. The endpoint requires `maintenance.read` and derives its values from:

- the versioned in-process Agent registry;
- persisted Agent graph schedules;
- governed proposals and their review state;
- entity claims, sections, relationships, and their citations;
- governed source definitions and source policy evaluation;
- persisted source acquisition jobs;
- the transactional outbox;
- publication releases and their persisted post-release verification checks.

Counts that can grow without bound use SQL aggregate queries. Only the recent
proposal and schedule lists are limited to eight rows for display. Evidence
coverage means the percentage of structured claims, sections, and relationships
that contain at least one citation identifier; it is not a semantic quality
score.

The response includes `generatedAt` and `dataVersion` so clients can display the
freshness and publication context of the snapshot. The Admin client polls this
endpoint every 15 seconds through its same-origin backend proxy.

The latest-release panel renders the persisted verification state rather than
assuming that `published` means healthy. Admin `/releases` exposes individual
revision, citation, relationship, alias, and discovery checks, their attempt
number, affected entity IDs, and any automatic rollback reason.

## Operational boundary

This read model is intentionally application-level and database-portable. It is
not a replacement for infrastructure telemetry. Request latency, error rate,
queue age, database saturation, and worker health belong in the metrics and
tracing stack under `infra/observability`.

Every new Admin statistic must name its persisted source and its interpretation
before being added. Values that cannot be computed from real state should be
rendered as unavailable rather than replaced by constants.
