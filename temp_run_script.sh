#!/usr/bin/env bash
set -euo pipefail

# Examples for this host: 8 H200s, 160 logical CPUs, ~1.6 TiB RAM.
# Run as your normal user, not with sudo. With no arguments, only print usage.
# Existing docker-group membership is refreshed automatically when needed.
# SERVER_WAIT_TIMEOUT=900 allows up to 15 minutes for an already-starting server.
#
# Terminal 1 (setup once, then keep serving in the foreground):
#   bash temp_run_script.sh setup
#   bash temp_run_script.sh serve
# Terminal 2 (after the server is ready and benchmark images are downloaded):
#   bash temp_run_script.sh check
#   bash temp_run_script.sh smoke
# Inspect smoke summaries/logs, then run generation followed by evaluation:
#   bash temp_run_script.sh full
# OpenCode is found on PATH or at ~/.opencode/bin/opencode (no .bashrc needed).
# OPENCODE_BIN=/absolute/path/to/opencode overrides executable discovery.
# Choose a harness or override concurrency explicitly:
#   HARNESS=opencode CONCURRENCY=8 MAX_WORKERS=16 bash temp_run_script.sh full
# Artifacts default to patcheval/exp_agent/agent_runs/; RUNS_DIR overrides it.
# Relative RUNS_DIR and evaluation paths are relative to the repository root.
# Model caches and the serving environment remain under RUNTIME_DIR.
# Re-evaluate an existing generation run, without generating again:
#   MAX_WORKERS=16 bash temp_run_script.sh evaluate /absolute/path/to/generation/run
#
# CONCURRENCY=8 matches DP=8 * max-num-seqs=1 active model requests.
# vLLM's limit is per replica:
# https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/
# Try 16 generation tasks only after measuring an 8-task baseline: tasks doing
# tool work can leave GPUs idle, but extra tasks also add queueing to agent timeouts.
# Keep server max-num-seqs=1 initially for the 262,144-token context. Increasing
# it requires restarting the server and measuring GPU memory at long contexts.
# MAX_WORKERS=16 is a starting point, not a measured optimum. Evaluation is
# CPU/I/O work, not GPU inference; containers have no CPU cap and builds can
# spawn many threads. Compare 8/16/32 workers on the SAME completed run; select
# the fastest setting that does not introduce resource failures or timeouts.
# Generation and evaluation run sequentially to reduce CPU/I/O contention.
# AGENT_TIMEOUT includes model queueing and tool execution; timed-out tasks
# submit empty patches. Set a suitable budget explicitly for your experiment.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${RUNTIME_DIR:-/mnt/local/patcheval-local-llm}"
RUNS_DIR="${RUNS_DIR:-${REPO_ROOT}/patcheval/exp_agent/agent_runs}"
# Shell-created invocation directories use the same base as Hydra paths.
cd "$REPO_ROOT"
RUNS_DIR="$(realpath -m "$RUNS_DIR")"
SERVER_HOST="${SERVER_HOST:-172.17.0.1}"
SERVER_PORT="${SERVER_PORT:-30000}"
SERVER_WAIT_TIMEOUT="${SERVER_WAIT_TIMEOUT:-900}"
HARNESS="${HARNESS:-codex}"
CONCURRENCY="${CONCURRENCY:-8}"
MAX_WORKERS="${MAX_WORKERS:-16}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-3600}"

workflow=(bash "${REPO_ROOT}/scripts/run.sh"
  "paths.runtime=${RUNTIME_DIR}" "paths.runs=${RUNS_DIR}"
  "server.host=${SERVER_HOST}" "server.port=${SERVER_PORT}" "harness=${HARNESS}")

# A sudo invocation loses user harness credentials and creates root-owned runs.
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
  echo "Run this script without sudo; it refreshes existing Docker group membership itself." >&2
  exit 1
fi

ensure_docker() {
  local message docker_gid command
  if message="$(docker info 2>&1)"; then
    return
  fi
  # Refresh only an existing membership, and only for a socket permission error.
  # Re-exec the whole workflow so Python's Docker SDK inherits the group too.
  docker_gid="$(getent group docker | cut -d: -f3)"
  if [[ "$message" == *"permission denied"* && -n "$docker_gid" &&
        " $(id -G "$(id -un)") " == *" ${docker_gid} "* &&
        " $(id -G) " != *" ${docker_gid} "* &&
        "${PATCHEVAL_GROUP_REFRESHED:-0}" != 1 ]]; then
    echo "Refreshing existing docker-group membership for this workflow." >&2
    export PATCHEVAL_GROUP_REFRESHED=1
    printf -v command '%q ' bash "${REPO_ROOT}/temp_run_script.sh" "$@"
    exec sg docker -c "exec ${command}"
  fi
  printf '%s\n' "$message" >&2
  echo "Docker is unavailable. Check daemon/socket access; do not run the whole workflow with sudo." >&2
  return 1
}

wait_for_server() {
  # Poll model readiness without generating tokens or launching a server.
  uv run --project "$REPO_ROOT" python - "$SERVER_HOST" "$SERVER_PORT" "$SERVER_WAIT_TIMEOUT" <<'PYTHON'
import json
import os
import sys
import time
import urllib.error
import urllib.request

host, port, timeout = sys.argv[1:]
host = f"[{host}]" if ":" in host else host
url = f"http://{host}:{port}/v1/models"
deadline = time.monotonic() + float(timeout)
headers = {}
key = os.environ.get("VLLM_API_KEY") or os.environ.get("SGLANG_API_KEY")
if key:
    headers["Authorization"] = "Bearer " + key
print(f"Waiting up to {timeout}s for Qwen/Qwen3.8-27B at {url}...", flush=True)
while True:
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            models = json.load(response)
        if any(model.get("id") == "Qwen/Qwen3.8-27B" for model in models.get("data", [])):
            print("Model endpoint ready.", flush=True)
            break
        raise ValueError("Qwen/Qwen3.8-27B is not advertised by the endpoint")
    except (OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
            raise SystemExit("Endpoint authentication failed; set VLLM_API_KEY to match the server.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(f"Server not ready: {exc}\nStart 'bash temp_run_script.sh serve' in another terminal, "
                             "inspect its logs, and verify SERVER_HOST/SERVER_PORT. No server was started by this check.")
        time.sleep(min(5, remaining))
PYTHON
}

case "${1:-help}" in
  setup)
    "${workflow[@]}" action=setup
    ;;
  serve)
    "${workflow[@]}" action=serve 'server.gpu="0,1,2,3,4,5,6,7"' \
      server.tensor_parallel=1 server.data_parallel=8 \
      server.max_running_requests=1 model.context_length=262144
    ;;
  check)
    wait_for_server
    "${workflow[@]}" action=check
    ;;
  smoke|full)
    experiment="$1"
    concurrency="$CONCURRENCY"
    workers="$MAX_WORKERS"
    if [[ "$experiment" == smoke ]]; then
      concurrency=1
      workers=1
    fi
    # Requires Docker socket access and the selected cases' images locally.
    # Docker AND containerd storage must be on /mnt/local (see AGENTS.md).
    ensure_docker "$@"
    wait_for_server
    "${workflow[@]}" action=check
    # An isolated Hydra invocation makes the evaluation input unambiguous.
    mkdir -p "${RUNS_DIR}/hydra"
    job_dir="$(mktemp -d "${RUNS_DIR}/hydra/vllm-${experiment}-XXXXXXXX")"
    label="qwen_vllm_${HARNESS}_${experiment}_$(date -u +%Y%m%d_%H%M%S)"
    "${workflow[@]}" action=generate "experiment=${experiment}" "label=${label}" \
      "hydra.run.dir=${job_dir}" "generation.concurrency=${concurrency}" \
      "generation.timeout=${AGENT_TIMEOUT}" generation.save_trajectories=true
    shopt -s nullglob
    generation_runs=("${job_dir}"/generation/*)
    if [[ ${#generation_runs[@]} -ne 1 || ! -f "${generation_runs[0]}/summary.json" ]]; then
      echo "Expected one completed generation run under ${job_dir}/generation" >&2
      exit 1
    fi
    run_dir="${generation_runs[0]}"
    printf 'Evaluating generation run: %s\n' "$run_dir"
    "${workflow[@]}" action=evaluate "label=${label}" \
      "evaluation.run_dir=${run_dir}" "evaluation.max_workers=${workers}"
    ;;
  evaluate)
    if [[ $# -ne 2 ]]; then
      echo "Usage: bash temp_run_script.sh evaluate /absolute/path/to/generation/run" >&2
      exit 2
    fi
    ensure_docker "$@"
    "${workflow[@]}" action=evaluate "evaluation.run_dir=$2" \
      "evaluation.max_workers=${MAX_WORKERS}"
    ;;
  help|-h|--help)
    echo 'Usage: bash temp_run_script.sh {setup|serve|check|smoke|full|evaluate RUN_DIR}'
    echo 'Defaults: CONCURRENCY=8 MAX_WORKERS=16 HARNESS=codex AGENT_TIMEOUT=3600'
    echo 'Start serve in another terminal, run smoke, inspect outputs, then run full.'
    echo 'See comments in this script for overrides and tuning guidance.'
    ;;
  *)
    echo "Unknown action: $1 (use --help)" >&2
    exit 2
    ;;
esac
