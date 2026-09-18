#!/usr/bin/env bash
# Example: evaluate_patches.sh label=my_run evaluation.run_dir=/path/to/run
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/run.sh" "$@" action=evaluate
