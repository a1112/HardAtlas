# Skeleton verification — 2026-07-28

## Passed

- Requirements archive SHA-256 verification: passed, byte-identical source.
- Image 2 batch validation: 8/8 jobs accepted by the bundled CLI dry-run with
  `gpt-image-2`, `high`, `3840x2160`, PNG.
- TypeScript: 17 Turbo check/test tasks passed.
- Production frontend build: Web, Admin, Desktop and all TypeScript packages
  passed.
- Python: Ruff check and format passed; pytest 8/8 passed, including the core
  compatibility golden set and API contract fixtures.
- Rust: scanner-core and scanner-windows tests 2/2 passed.
- Tauri: native release build passed at
  `target/release/hardatlas-desktop`.
- OpenAPI: schema and generated TypeScript client reproduced identical SHA-256
  values on a second generation.
- Configuration: JSON and Compose/OpenTelemetry YAML parsed successfully.

## Environment-limited checks

- Docker is not installed on the current machine, so PostgreSQL, Redis,
  OpenSearch, MinIO, Keycloak and OpenTelemetry health checks were not started.
- `OPENAI_API_KEY` is not set, so no billable Image 2 generation request was
  made and the eight final PNG files remain pending.

## Non-blocking warning

FastAPI's current test client emitted an upstream Starlette deprecation warning
about the future `httpx2` test client. It does not affect the passing API tests.
