#!/usr/bin/env bash
# Example: serve_sglang.sh serve server.gpu=0 paths.runtime=/mnt/local/llm
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:---help}" in
  setup|serve|check) action="$1"; shift ;;
  -h|--help) exec bash "${SCRIPT_DIR}/../run.sh" --help ;;
  *) action=serve ;;
esac
exec bash "${SCRIPT_DIR}/../run.sh" "$@" "action=${action}"
