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
AGENT_READY_PATTERN=""
# shellcheck source=/dev/null
source "$AGENT_FILE"

DATASET="${DATASET:-${SCRIPT_DIR}/../datasets/patcheval_verified.json}"
OUTPUT_BASE="${OUTPUT_BASE:-${RUNS_DIR:-${SCRIPT_DIR}/agent_runs}}"

mount_args=()
for mount in "${AGENT_MOUNTS[@]:-}"; do
  mount_args+=(--mount "$mount")
done

# Startup watchdog: an agent that never prints its ready marker is retried in a
# fresh container (no model output exists yet, so results are unaffected).
startup_args=()
if [[ -n "$AGENT_READY_PATTERN" ]]; then
  startup_args+=(--ready-pattern "$AGENT_READY_PATTERN"
    --startup-timeout "${STARTUP_TIMEOUT:-300}" --startup-retries "${STARTUP_RETRIES:-2}")
fi
# Resume: rerun selected cases inside an existing run directory.
if [[ -n "${RERUN_CVES_FILE:-}" ]]; then
  startup_args+=(--only-cves-file "$RERUN_CVES_FILE")
fi
if [[ -n "${RERUN_INTO:-}" ]]; then
  startup_args+=(--rerun-into "$RERUN_INTO")
fi

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
  "${startup_args[@]}" \
  --agent-command "$AGENT_COMMAND" \
  --agent-timeout "${AGENT_TIMEOUT:-2400}" \
  --container-prefix "patcheval-${AGENT}"
