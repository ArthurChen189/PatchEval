#!/usr/bin/env bash
# 🚀 PatchEval Hydra CLI (in PatchEval's uv env)
# 
# Recommended commands (run from repository root):
#
# 🛠️  Setup (specify your runtime directory):
#   bash scripts/run.sh action=setup paths.runtime=/mnt/local/patcheval-local-llm
#
# ▶️  Start Model Server:
#   bash scripts/run.sh action=serve paths.runtime=/mnt/local/patcheval-local-llm
#
# 🔎  Check Server (run in a separate terminal):
#   bash scripts/run.sh action=check
#
# 🧪  Generate Patches for Smoke Test:
#   # Codex
#   bash scripts/run.sh action=generate experiment=smoke harness=codex \
#     label=qwen_codex_smoke harness.binary="$(command -v codex)"
#
#   # OpenCode
#   bash scripts/run.sh action=generate experiment=smoke harness=opencode \
#     label=qwen_opencode_smoke harness.binary=/absolute/path/to/opencode
#
# 🌐  Use non-default endpoint: 
#     append server.base_url=http://HOST:30000/v1
#
# 📊  Full Benchmark Run:
#     experiment=full     # Use after inspecting smoke test results
#     label=new_label     # Each run should have a unique label
#     generation.timeout=3600   # (optional) Adjust agent timeout if needed
#
# 📂  Results:
#     Generation prints the run directory.
#     Patches are in: <run_dir>/patches/
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --project "$REPO_ROOT" python "${REPO_ROOT}/scripts/run.py" "$@"
