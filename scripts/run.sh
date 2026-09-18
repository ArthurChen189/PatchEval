#!/usr/bin/env bash
# Bootstrap the shared Hydra CLI in PatchEval's uv environment.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --project "$REPO_ROOT" python "${REPO_ROOT}/scripts/run.py" "$@"
