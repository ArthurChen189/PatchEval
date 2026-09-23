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
# Codex 0.155.0 and OpenCode 1.18.31 are pinned: generation uses the vendored
# binaries under third_party/ (extracted and checksum-verified on first use) and
# refuses other versions. CODEX_BIN/OPENCODE_BIN override them only together
# with a matching harness.version.
# Choose model (Hydra group under scripts/conf/model/), harness, or concurrency:
#   MODEL=qwen3_5_9b HARNESS=opencode CONCURRENCY=48 MAX_WORKERS=16 bash temp_run_script.sh full
#   MODEL=qwen3_8_27b bash temp_run_script.sh serve   # same MODEL for serve/check/generate
# Artifacts default to patcheval/exp_agent/agent_runs/; RUNS_DIR overrides it.
# Relative RUNS_DIR and evaluation paths are relative to the repository root.
# Model caches and the serving environment remain under RUNTIME_DIR.
# SAMPLES=4 runs four independent generations per case for pass@4; SAMPLES=1
# gives a quicker smoke test. Re-evaluate without generating again, passing a
# single run, any sample run, or a multi-sample invocation directory; every
# sample of the invocation is evaluated and pass_at_k.json is written:
#   MAX_WORKERS=16 bash temp_run_script.sh evaluate /absolute/path/to/generation/run
# Rerun tasks whose agent never started (startup watchdog), generate missing
# samples (same settings), then evaluate all. Wait until the run has finished:
#   MAX_WORKERS=16 bash temp_run_script.sh resume '/path/to/agent_runs/Qwen3.8-27B_OpenCode_Max-output-token=16k/<time>-generate-opencode-<label>'
#
# Serving defaults (scripts/conf/config.yaml) run 8 replicas x 8 requests
# (max-num-seqs is per replica) with MTP speculative decoding, prefix caching,
# and chunked prefill. MODEL selects the checkpoint YAML (path / parsers /
# output cap); keep the same MODEL for serve, check, smoke, full, and resume.
# CONCURRENCY=48 keeps ~6 agent sessions per GPU while agents are in tools:
# https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/
# More concurrency also slows each request, so the fixed agent timeout binds
# sooner; compare timeouts before raising it. Restart the server to apply new
# serving or model settings, but never while a generation run is in progress.
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
# Hydra model group name under scripts/conf/model/ (not the HF path).
MODEL="${MODEL:-qwen3_5_9b}"
export MODEL
CONCURRENCY="${CONCURRENCY:-48}"
MAX_WORKERS="${MAX_WORKERS:-16}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-2400}"
# Independent generation runs per case; evaluation reports pass@1..pass@SAMPLES.
SAMPLES="${SAMPLES:-4}"

MODEL_CONFIG="${REPO_ROOT}/scripts/conf/model/${MODEL}.yaml"
if [[ ! -f "$MODEL_CONFIG" ]]; then
  echo "Unknown MODEL=${MODEL}: expected ${MODEL_CONFIG}" >&2
  echo "Available: $(find "${REPO_ROOT}/scripts/conf/model" -name '*.yaml' -printf '%f\n' | sed 's/\.yaml$//' | paste -sd, -)" >&2
  exit 2
fi
# path: is the HF id; YAML served_name defaults to ${model.path}, so wait/check use path.
MODEL_ID="$(awk -F': *' '
  /^path:/ {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
    gsub(/^["'\'']|["'\'']$/, "", $2)
    print $2
    exit
  }
' "$MODEL_CONFIG")"
if [[ -z "$MODEL_ID" ]]; then
  echo "MODEL=${MODEL} config is missing a path: field (${MODEL_CONFIG})" >&2
  exit 2
fi

workflow=(bash "${REPO_ROOT}/scripts/run.sh"
  "paths.runtime=${RUNTIME_DIR}" "paths.runs=${RUNS_DIR}"
  "server.host=${SERVER_HOST}" "server.port=${SERVER_PORT}" "harness=${HARNESS}" "model=${MODEL}")

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
  # Poll readiness for MODEL_ID (from scripts/conf/model/${MODEL}.yaml) without
  # generating tokens or launching a server.
  uv run --project "$REPO_ROOT" python - "$SERVER_HOST" "$SERVER_PORT" "$SERVER_WAIT_TIMEOUT" "$MODEL_ID" <<'PYTHON'
import json
import os
import sys
import time
import urllib.error
import urllib.request

host, port, timeout, model_id = sys.argv[1:]
host = f"[{host}]" if ":" in host else host
url = f"http://{host}:{port}/v1/models"
deadline = time.monotonic() + float(timeout)
headers = {}
key = os.environ.get("VLLM_API_KEY") or os.environ.get("SGLANG_API_KEY")
if key:
    headers["Authorization"] = "Bearer " + key
print(f"Waiting up to {timeout}s for {model_id} at {url}...", flush=True)
while True:
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            models = json.load(response)
        if any(model.get("id") == model_id for model in models.get("data", [])):
            print(f"Model endpoint ready ({model_id}).", flush=True)
            break
        advertised = ", ".join(m.get("id", "?") for m in models.get("data", [])) or "(none)"
        raise ValueError(f"{model_id} is not advertised by the endpoint (have: {advertised})")
    except (OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
            raise SystemExit("Endpoint authentication failed; set VLLM_API_KEY to match the server.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(
                f"Server not ready: {exc}\n"
                f"Start 'MODEL={os.environ.get('MODEL', '<model>')} bash temp_run_script.sh serve' "
                "in another terminal with the same MODEL, inspect its logs, and verify "
                "SERVER_HOST/SERVER_PORT. No server was started by this check."
            )
        time.sleep(min(5, remaining))
PYTHON
}

case "${1:-help}" in
  setup)
    "${workflow[@]}" action=setup
    ;;
  serve)
    # Checkpoint comes from model=${MODEL}; batching/MTP/caching from config.yaml.
    printf 'Serving %s (%s)\n' "$MODEL" "$MODEL_ID"
    "${workflow[@]}" action=serve 'server.gpu="0,1,2,3,4,5,6,7"' \
      server.tensor_parallel=1 server.data_parallel=8
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
    # Hydra places the generation in <model>_<Harness>_Max-output-token=<cap>/
    # under RUNS_DIR (see AGENTS.md); the unique label identifies it for evaluation.
    label="${MODEL}_${HARNESS}_${experiment}_$(date -u +%Y%m%d_%H%M%S)"
    printf 'Using model %s (%s)\n' "$MODEL" "$MODEL_ID"
    "${workflow[@]}" action=generate "experiment=${experiment}" "label=${label}" \
      "generation.concurrency=${concurrency}" \
      "generation.timeout=${AGENT_TIMEOUT}" "generation.samples=${SAMPLES}" \
      generation.save_trajectories=true
    # Evaluation checks that every configured sample completed, evaluates each,
    # and writes pass_at_k.json in its invocation directory, next to the run.
    printf 'Evaluating %s generation sample(s) labelled: %s\n' "$SAMPLES" "$label"
    "${workflow[@]}" action=evaluate "label=${label}" "evaluation.max_workers=${workers}"
    ;;
  resume)
    if [[ $# -ne 2 ]]; then
      echo "Usage: bash temp_run_script.sh resume /path/to/generation/invocation" >&2
      exit 2
    fi
    # Generates only the missing samples, with the invocation's own recorded
    # model, harness, label, and generation settings, then evaluates all samples.
    # Align wait/check with that invocation so shell MODEL need not match.
    resume_resolved="$2/resolved.yaml"
    if [[ ! -f "$resume_resolved" ]]; then
      echo "Not a generation invocation (no resolved.yaml): $2" >&2
      exit 2
    fi
    resume_model_id="$(awk '
      /^model:/ { in_model=1; next }
      in_model && /^[^[:space:]#]/ { exit }
      in_model && /^[[:space:]]+path:/ {
        sub(/^[[:space:]]+path:[[:space:]]*/, "")
        gsub(/^["'\'']|["'\'']$/, "")
        print
        exit
      }
    ' "$resume_resolved")"
    if [[ -z "$resume_model_id" ]]; then
      echo "Could not read model.path from ${resume_resolved}" >&2
      exit 2
    fi
    MODEL_ID="$resume_model_id"
    printf 'Resuming with served model %s (from %s)\n' "$MODEL_ID" "$2"
    ensure_docker "$@"
    wait_for_server
    "${workflow[@]}" action=check "model.path=${MODEL_ID}" "model.served_name=${MODEL_ID}"
    "${workflow[@]}" action=generate "generation.resume_dir=$2"
    "${workflow[@]}" action=evaluate "evaluation.run_dir=$2" \
      "evaluation.max_workers=${MAX_WORKERS}"
    ;;
  evaluate)
    if [[ $# -ne 2 ]]; then
      echo "Usage: bash temp_run_script.sh evaluate /path/to/generation/run-sample-or-invocation" >&2
      exit 2
    fi
    ensure_docker "$@"
    # Any sample run path evaluates every sample of its invocation. PARTIAL=1
    # scores only completed samples when some are missing (marked partial).
    "${workflow[@]}" action=evaluate "evaluation.run_dir=$2" \
      "evaluation.max_workers=${MAX_WORKERS}" "evaluation.allow_partial=${PARTIAL:-false}"
    ;;
  help|-h|--help)
    echo 'Usage: bash temp_run_script.sh {setup|serve|check|smoke|full|resume INVOCATION|evaluate RUN_DIR}'
    echo "Defaults: MODEL=${MODEL} (${MODEL_ID}) CONCURRENCY=48 MAX_WORKERS=16 HARNESS=codex AGENT_TIMEOUT=2400 SAMPLES=4"
    echo 'MODEL is a Hydra group under scripts/conf/model/ (e.g. qwen3_5_9b, qwen3_8_27b).'
    echo 'Use the same MODEL for serve and for check/smoke/full/resume against that endpoint.'
    echo 'Start serve in another terminal, run smoke, inspect outputs, then run full.'
    echo 'See comments in this script for overrides and tuning guidance.'
    ;;
  *)
    echo "Unknown action: $1 (use --help)" >&2
    exit 2
    ;;
esac
