# Contract tests

OpenAPI is the sole HTTP contract. CI regenerates the schema and TypeScript
types, checks for an empty diff, then runs Schemathesis against the FastAPI app.
The API skeleton tests live in `apps/api/tests`.
