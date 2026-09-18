#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <prefix> [runner-output-dir]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
# Keep the legacy positional/environment interface, using Hydra's isolated
# conversion, report, log, and configuration directories for every invocation.
args=(action=evaluate "label=$1"
  "paths.runs=${RUNS_DIR:-${OUTPUT_BASE:-${SCRIPT_DIR}/agent_runs}}"
  "paths.dataset=${DATASET:-${REPO_ROOT}/patcheval/datasets/patcheval_verified.json}"
  "evaluation.max_workers=${MAX_WORKERS:-4}"
  "evaluation.log_level=${LOG_LEVEL:-INFO}")
if [[ $# -eq 2 ]]; then
  args+=("evaluation.run_dir=$2")
fi
if [[ -n "${EVALUATION_OUTPUT_DIR:-}" ]]; then
  args+=("hydra.run.dir=${EVALUATION_OUTPUT_DIR}")
fi
exec python "${REPO_ROOT}/scripts/run.py" "${args[@]}"
