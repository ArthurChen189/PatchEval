#!/usr/bin/env bash
# Example: generate_patches.sh harness=codex experiment=smoke label=my_run
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/run.sh" "$@" action=generate
