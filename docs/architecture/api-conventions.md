# API conventions

- Base path: `/api/v1`.
- Media type: JSON encoded as UTF-8.
- Entity IDs: UUIDv7 strings; old merged IDs remain redirects.
- Errors: RFC 9457 problem details with a stable `error_code`.
- Pagination: opaque cursor with deterministic ordering.
- Writes: idempotency key for retryable create operations.
- Conditional reads: ETag for public/versioned resources.
- Long operations: a persisted job resource plus server-sent events; progress is
  derived from acquisition, graph schedule and graph run state rather than
  synthesized in the HTTP layer.
- Every decision response includes `data_version` and `rule_version`.
- Public timestamps are RFC 3339 UTC.

## Initial contracts

| Method | Path                                              | Purpose                                      |
| ------ | ------------------------------------------------- | -------------------------------------------- |
| GET    | `/api/v1/health`                                  | Process liveness only                        |
| GET    | `/api/v1/ready`                                   | Database/search/registry readiness           |
| GET    | `/api/v1/build`                                   | Build, schema and data/rule versions         |
| GET    | `/api/v1/search`                                  | Search, browse, facets and entity candidates |
| POST   | `/api/v1/answers`                                 | Persist an evidence-grounded public answer   |
| GET    | `/api/v1/answers/{answer_id}`                     | Replay the exact persisted answer            |
| GET    | `/api/v1/entities/{slug}`                         | Current published entity revision            |
| GET    | `/api/v1/entities/{slug}/revisions/{revision_id}` | Immutable revision                           |
| GET    | `/api/v1/jobs/{job_id}/events`                    | SSE progress stream                          |

`GET /jobs/{job_id}/events` emits one current-state event by default. Clients that
need a live stream pass `follow=true`; the server then polls persisted state at
the bounded `intervalMs` cadence until a terminal status is reached. Unknown job
IDs return `404`. The Admin BFF streams the response body and preserves
`Content-Type`, `Cache-Control` and `X-Request-Id`.

`/health` never probes dependencies and is suitable for a liveness check.
`/ready` returns `200` only when the database, search backend, Domain Registry,
Agent Registry and Parser Registry are usable; otherwise it returns `503` with
the individual boolean checks and no credentials or connection strings.

Current entity responses use a revision-derived ETag with a short public cache.
Fixed revision URLs use the same ETag plus
`Cache-Control: public, max-age=31536000, immutable`; matching
`If-None-Match` requests return `304`.
