#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <codex|opencode|traecli> [prefix]" >&2
  exit 2
fi

AGENT="$1"
PREFIX="${2:-$AGENT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
AGENT_FILE="${SCRIPT_DIR}/agents/${AGENT}.sh"
if [[ ! -f "$AGENT_FILE" ]]; then
  echo "Unknown agent '$AGENT'; expected one of: codex opencode traecli" >&2
  exit 2
fi

AGENT_TRAJECTORY_PATHS=()
# shellcheck source=/dev/null
source "$AGENT_FILE"

DATASET="${DATASET:-${SCRIPT_DIR}/../datasets/patcheval_verified.json}"
OUTPUT_BASE="${OUTPUT_BASE:-${RUNS_DIR:-${SCRIPT_DIR}/agent_runs}}"

mount_args=()
for mount in "${AGENT_MOUNTS[@]:-}"; do
  mount_args+=(--mount "$mount")
done

trajectory_args=()
case "${SAVE_TRAJECTORIES:-false}" in
  true|1)
    trajectory_args+=(--save-trajectories)
    for path in "${AGENT_TRAJECTORY_PATHS[@]}"; do
      trajectory_args+=(--trajectory-path "$path")
    done
    ;;
  false|0) ;;
  *) echo "SAVE_TRAJECTORIES must be true/false or 1/0" >&2; exit 2 ;;
esac

python "${SCRIPT_DIR}/patch_agent_runner.py" \
  --input "$DATASET" \
  --output-dir "$OUTPUT_BASE" \
  --limit "${LIMIT:--1}" \
  --concurrency "${CONCURRENCY:-4}" \
  --run-label "$PREFIX" \
  "${mount_args[@]}" \
  "${AGENT_EXTRA_ARGS[@]}" \
  "${trajectory_args[@]}" \
  --agent-command "$AGENT_COMMAND" \
  --agent-timeout "${AGENT_TIMEOUT:-2400}" \
  --container-prefix "patcheval-${AGENT}"
