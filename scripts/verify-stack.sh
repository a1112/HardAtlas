#!/usr/bin/env bash
set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$workspace_dir"

echo "▶ 1) Archive integrity"
bash scripts/verify_archive.sh

echo "▶ 2) Type and code checks"
pnpm run check

echo "▶ 3) Frontend/backend build"
pnpm build

echo "▶ 4) Python contract + behavior tests"
uv run pytest

echo "▶ 5) Rust workspace check"
cargo check --workspace

echo "▶ 6) End-to-end contract validation"
pnpm test:e2e

echo "▶ 7) UI image artifacts"
pnpm run verify:image-assets

echo "✅ All verification gates passed."
