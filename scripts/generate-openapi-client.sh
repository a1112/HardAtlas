#!/usr/bin/env bash
set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$workspace_dir"

uv run --package hardatlas-api python scripts/export_openapi.py
corepack pnpm exec openapi-typescript apps/api/openapi.json \
  --output packages/ts/api-client/src/generated.ts
corepack pnpm exec prettier --write \
  apps/api/openapi.json \
  packages/ts/api-client/src/generated.ts
