# OpenCode agent adapter. Source this file from run_infer.sh.
#
# Required:
#   OPENCODE_CONFIG=/path/to/opencode-home/config/opencode/opencode.json
# Optional:
#   OPENCODE_BIN=/path/to/opencode (default: the vendored pinned release)
#   OPENCODE_VERSION=<x.y.z>      (default: OPENCODE_PINNED_VERSION; empty skips the check)
#
# OPENCODE_CONFIG is the single config input. The adapter derives the XDG config
# and data roots from it. Config is mounted read-only; data is mounted read-only
# as a seed and copied to /tmp inside each case container so concurrent runs do
# not write to the same host state/log files.

# Keep in sync with scripts/conf/harness/opencode.yaml.
OPENCODE_PINNED_VERSION=1.18.31
OPENCODE_VENDORED_BIN=third_party/opencode/1.18.31/opencode-linux-x64
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
AGENT_COMMAND='rm -rf /tmp/opencode-config /tmp/opencode-data && mkdir -p /tmp/opencode-config /tmp/opencode-data && cp -a /opt/opencode-config-src/. /tmp/opencode-config/ && cp -a /opt/opencode-data-src/. /tmp/opencode-data/ && XDG_CONFIG_HOME=/tmp/opencode-config XDG_DATA_HOME=/tmp/opencode-data opencode run --format json --auto < {prompt_file}'

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
