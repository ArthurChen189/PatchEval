#!/usr/bin/env bash
# Bootstrap the shared Hydra CLI in PatchEval's uv environment.
# From the repository root (default model: Qwen/Qwen3.8-27B):
#
#   bash scripts/run.sh action=setup paths.runtime=/mnt/local/patcheval-local-llm
#   bash scripts/run.sh action=serve paths.runtime=/mnt/local/patcheval-local-llm
#   # In another terminal:
#   bash scripts/run.sh action=check
#
#   bash scripts/run.sh action=generate experiment=smoke harness=codex \
#     label=qwen_codex_smoke harness.binary="$(command -v codex)"
#   bash scripts/run.sh action=generate experiment=smoke harness=opencode \
#     label=qwen_opencode_smoke harness.binary=/absolute/path/to/opencode
#
#   Non-default endpoint: append server.base_url=http://HOST:30000/v1
#   Full run: experiment=full with a new label; optional generation.timeout=3600
#   Generation prints the run dir; patches land in its patches/ subdirectory.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --project "$REPO_ROOT" python "${REPO_ROOT}/scripts/run.py" "$@"
