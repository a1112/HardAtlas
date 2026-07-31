#!/usr/bin/env bash
set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$workspace_dir/docs/product/requirements/v1.0"
shasum -a 256 --check SHA256SUMS
