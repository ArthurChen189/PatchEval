# Repository Guidelines

## Project Structure & Module Organization

PatchEval evaluates agent-generated vulnerability repairs in Docker environments.

- `patcheval/evaluation/`: patch evaluator, shared utilities, and example input.
- `patcheval/exp_agent/`: patch-generation runner, conversion scripts, and CLI adapters in `agents/`.
- `patcheval/datasets/patcheval_verified.json`: metadata for 230 verified CVE cases.
- `scripts/`: Docker image downloader and `images.txt` manifest.
- `tests/`: Python unit tests; `docs/` contains submission instructions and figures.

Keep generation and evaluation separate. Generated artifacts belong under `agent_runs/`, `eval_inputs/`, and `evaluation_output/`, not alongside source files.

## Build, Test, and Development Commands

Use Linux, Python 3.10+ (3.12 recommended), and an accessible Docker daemon. There is no separate build step. Activate your Python environment first.

From the repository root:

- `pip install -r requirements.txt`: install runtime dependencies.
- `python -m unittest discover -s tests -v`: run unit tests.
- `(cd scripts && python download_images.py)`: pull all benchmark images; reserve at least 500 GB for the full collection.

For a one-case integration smoke test, configure the agent executable and credentials as described in `patcheval/exp_agent/README.md`, then run:

```bash
cd patcheval/exp_agent
LIMIT=1 CONCURRENCY=1 bash run_infer.sh codex smoke
MAX_WORKERS=1 bash run_eval.sh smoke
```

Ensure the selected case's Docker image is available first.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and modules, and `PascalCase` classes. Follow nearby code for imports and type annotations. Bash scripts use two-space indentation and `set -euo pipefail`; quote path variables. No repository-wide formatter or linter configuration is present.

## Testing Guidelines

Tests use standard-library `unittest` and `unittest.mock`. Name files `test_*.py` and methods `test_*`. Mock Docker operations in unit tests and cover failure statuses as well as success. No coverage threshold is configured. For runner or evaluator changes, also run a one-case Docker smoke test and inspect summaries and logs.

## Commit & Pull Request Guidelines

Recent commits use concise prefixes such as `fix:` and `docs:`. Follow that style with an imperative summary. PRs should explain the problem, changed behavior, relevant issue links, and validation commands/results. Document required configuration or dataset changes.

## Configuration & Benchmark Integrity

Keep credentials, runtime homes, and generated logs outside version control. Do not use reference `patch_url`, `fix_func`, or bundled reference patches as repair sources. Keep dataset `image_url` entries and `scripts/images.txt` synchronized when updating cases.
