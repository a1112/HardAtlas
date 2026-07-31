# Atlas V2 verification — 2026-07-28

## Framework acceptance

- One generic `KnowledgeEntity` contract renders animal, plant, and electronic
  component fixtures.
- Declarative domain packs for animals, plants, and electronics load without
  application-specific code.
- Domain-pack validation checks referenced attributes, entity types, and view
  definitions.
- View definitions use a closed, safe block vocabulary rather than generated
  executable UI code.
- Public pages resolve spaces, taxonomy, entities, entity types, and view
  definitions from the API at request time; configured API failures do not
  silently fall back to fixtures.
- SQLAlchemy persistence stores immutable entity and Schema revisions,
  taxonomy documents, governed proposals, releases, outbox events, and Agent
  graph checkpoints.
- The `knowledge-maintenance` Agent DAG supports dependency validation,
  bounded retry, persisted checkpoints, idempotent resume, and proposal-only
  output.
- Hardware compatibility remains available only under the extension API.

## Passed checks

- TypeScript: all seven workspace packages pass type checks; contract tests
  pass and packages without tests exit explicitly through `--passWithNoTests`.
- Python: Ruff check/format and 26 pytest tests.
- Frontend production builds: Web, Admin, Desktop.
- Rust/Tauri desktop check.
- OpenAPI schema and TypeScript client generation.
- Browser inspection: multilingual alias search, dynamic Snow Leopard entry,
  safe view blocks, Agent proposal policy evaluation, two-person approval, and
  the four-node Agent graph console all rendered and completed successfully.
- API lifecycle: stage, publish, rollback, process restart, and persisted-state
  restoration passed.

## Image generation

- The first Atlas V2 search/taxonomy homepage was generated successfully using
  the Codex built-in image route.
- The remaining image calls currently fail at the Codex image endpoint with a
  network error. The local proxy remains active and records the requests through
  its GLOBAL route, so the failure is not caused by a missing API key.
- The latest direct built-in retry for screen 02 returned the same endpoint
  network error; no alternate model or CLI fallback was used.
