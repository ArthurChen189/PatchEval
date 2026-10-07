# OpenCode agent adapter. Source this file from run_infer.sh.
#
# Required:
#   OPENCODE_CONFIG=/path/to/opencode-home/config/opencode/opencode.json
# Optional:
#   OPENCODE_BIN=/path/to/opencode (default: the vendored pinned release)
#   OPENCODE_VERSION=<x.y.z>      (default: OPENCODE_PINNED_VERSION; empty skips the check)
#   RIPGREP_BIN=/path/to/rg       (default: the vendored pinned ripgrep; `none` mounts none)
#   RIPGREP_VERSION=<x.y.z>       (default: RIPGREP_PINNED_VERSION; empty skips the check)
#   OPENCODE_CONTINUE_ON_LENGTH=<n> (default 0: continue a run up to n times after a
#                                    response stops at the output-token limit)
#
# OPENCODE_CONFIG is the single config input. The adapter derives the XDG config
# and data roots from it. Config is mounted read-only; data is mounted read-only
# as a seed and copied to /tmp inside each case container so concurrent runs do
# not write to the same host state/log files.

# Keep in sync with scripts/conf/harness/opencode.yaml.
OPENCODE_PINNED_VERSION=1.18.31
OPENCODE_VENDORED_BIN=third_party/opencode/1.18.31/opencode-linux-x64
RIPGREP_PINNED_VERSION=15.1.0
RIPGREP_VENDORED_BIN=third_party/ripgrep/15.1.0/rg
# shellcheck source=../pinned_harness.sh
source "$(dirname "${BASH_SOURCE[0]}")/../pinned_harness.sh"

: "${OPENCODE_CONFIG:?Set OPENCODE_CONFIG to opencode.json, e.g. /path/to/opencode-home/config/opencode/opencode.json}"

AGENT_MOUNTS=()
AGENT_EXTRA_ARGS=()
pinned_harness_resolve OPENCODE_BIN "$OPENCODE_VENDORED_BIN"
if [[ ! -x "$OPENCODE_BIN" ]]; then
  echo "OPENCODE_BIN does not exist or is not executable: $OPENCODE_BIN" >&2
  exit 1
fi
pinned_harness_check OPENCODE_BIN "$OPENCODE_PINNED_VERSION"
OPENCODE_CONFIG="$(realpath "$OPENCODE_CONFIG")"
if [[ ! -f "$OPENCODE_CONFIG" ]]; then
  echo "OPENCODE_CONFIG does not exist or is not a file: $OPENCODE_CONFIG" >&2
  exit 1
fi
OPENCODE_CONFIG_HOME="$(cd "$(dirname "$OPENCODE_CONFIG")/.." && pwd)"
OPENCODE_DATA_HOME="$(cd "${OPENCODE_CONFIG_HOME}/../data" && pwd)"

AGENT_MOUNTS+=("${OPENCODE_BIN}:/usr/local/bin/opencode:ro")
AGENT_MOUNTS+=("${OPENCODE_CONFIG_HOME}:/opt/opencode-config-src:ro")
AGENT_MOUNTS+=("${OPENCODE_DATA_HOME}:/opt/opencode-data-src:ro")
# OpenCode's grep, glob, and skill tools run ripgrep: it uses `rg` from PATH, else downloads
# ripgrep, which offline agent containers cannot. RIPGREP_BIN=none reproduces runs without it.
if [[ "${RIPGREP_BIN:-}" != none ]]; then
  pinned_harness_resolve RIPGREP_BIN "$RIPGREP_VENDORED_BIN"
  if [[ ! -x "$RIPGREP_BIN" ]]; then
    echo "RIPGREP_BIN does not exist or is not executable: $RIPGREP_BIN" >&2
    exit 1
  fi
  pinned_harness_check RIPGREP_BIN "$RIPGREP_PINNED_VERSION"
  AGENT_MOUNTS+=("${RIPGREP_BIN}:/usr/local/bin/rg:ro")
fi
# Agent containers have no internet route (see patch_agent_runner.py --network).
# These flags stop OpenCode's own startup downloads (models.dev catalog, updates,
# default plugins, LSP servers, sharing), which would otherwise wait on the network;
# EXA would enable the websearch/codesearch tools.
OPENCODE_OFFLINE_ENV='OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_DEFAULT_PLUGINS=1 OPENCODE_DISABLE_LSP_DOWNLOAD=1 OPENCODE_DISABLE_SHARE=1'
# A response that spends the whole output budget on reasoning ends `opencode run` with nothing done
# ("reason":"length"). With OPENCODE_CONTINUE_ON_LENGTH>0 a mounted wrapper continues the same
# session with a fixed, task-neutral nudge (container/opencode_continue.sh); with 0 the command is
# unchanged. The XDG variables precede the wrapper so every continuation sees the same session data.
OPENCODE_CONTINUE_ON_LENGTH="${OPENCODE_CONTINUE_ON_LENGTH:-0}"
if [[ ! "$OPENCODE_CONTINUE_ON_LENGTH" =~ ^[0-9]+$ ]]; then
  echo "OPENCODE_CONTINUE_ON_LENGTH must be a non-negative integer" >&2
  exit 2
fi
OPENCODE_RUN='opencode run --format json --auto'
if (( OPENCODE_CONTINUE_ON_LENGTH > 0 )); then
  AGENT_MOUNTS+=("$(cd "$(dirname "${BASH_SOURCE[0]}")/../container" && pwd)/opencode_continue.sh:/opt/agent-tools/opencode-continue.sh:ro")
  OPENCODE_RUN="OPENCODE_CONTINUE_ON_LENGTH=${OPENCODE_CONTINUE_ON_LENGTH} OPENCODE_CONTINUE_RECORD_DIR=/results OPENCODE_CONTINUE_SNAPSHOT_FILE=/workspace/fix.patch bash /opt/agent-tools/opencode-continue.sh --format json --auto"
fi
AGENT_COMMAND="rm -rf /tmp/opencode-config /tmp/opencode-data && mkdir -p /tmp/opencode-config /tmp/opencode-data && cp -a /opt/opencode-config-src/. /tmp/opencode-config/ && cp -a /opt/opencode-data-src/. /tmp/opencode-data/ && unset OPENCODE_ENABLE_EXA OPENCODE_EXPERIMENTAL_EXA && ${OPENCODE_OFFLINE_ENV} XDG_CONFIG_HOME=/tmp/opencode-config XDG_DATA_HOME=/tmp/opencode-data ${OPENCODE_RUN} < {prompt_file}"

# Printed once OpenCode has opened and begun its first model step; the runner's
# startup watchdog retries a task in a fresh container if it never appears.
AGENT_READY_PATTERN='"type":"step_start"'

# Export session records only; never copy credential-bearing runtime homes.
AGENT_TRAJECTORY_PATHS=(
  "opencode.db=/tmp/opencode-data/opencode/opencode.db"
  "opencode.db-wal=/tmp/opencode-data/opencode/opencode.db-wal"
  "opencode.db-shm=/tmp/opencode-data/opencode/opencode.db-shm"
  "opencode_storage=/tmp/opencode-data/opencode/storage"
)
