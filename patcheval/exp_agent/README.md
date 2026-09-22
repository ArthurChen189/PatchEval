# PatchEval CLI Agent Patch Generation

This directory contains a lightweight patch-generation workflow for CLI coding agents.
It is designed to plug into the PatchEval-Verified evaluation flow:

```text
run_infer.sh + patch_agent_runner.py
  -> generate patches
  -> process_data.py
  -> ../evaluation/run_evaluation.py
  -> evaluate generated patches
```

The supported CLI agents share the same runner and differ only in their command and mounts. The shared runner handles Docker setup, prompt generation, patch collection, and output layout.

## Quickstart

Run one smoke case with Codex, then evaluate the generated patch:

```bash
conda activate patcheval
cd patcheval/exp_agent

# CODEX_BIN defaults to the pinned vendored Codex 0.155.0 under third_party/.
export CODEX_CONFIG=/path/to/codex-home/my-profile.config.toml

LIMIT=1 CONCURRENCY=1 bash run_infer.sh codex codex_smoke
bash run_eval.sh codex_smoke
```

For OpenCode or TraeCLI, set the corresponding runtime variables and replace
the agent name:

```bash
# OpenCode
conda activate patcheval
cd patcheval/exp_agent

# OPENCODE_BIN defaults to the pinned vendored OpenCode 1.18.31 under third_party/.
export OPENCODE_CONFIG=/path/to/opencode-home/config/opencode/opencode.json
LIMIT=1 CONCURRENCY=1 bash run_infer.sh opencode opencode_smoke
bash run_eval.sh opencode_smoke

# TraeCLI / TraeX
conda activate patcheval
cd patcheval/exp_agent

export TRAE_BIN=/path/to/bin/traex
export TRAE_CONFIG=/path/to/trae-home/my-profile.traecli.toml
LIMIT=1 CONCURRENCY=1 bash run_infer.sh traecli trae_smoke
bash run_eval.sh trae_smoke
```

Generated patches are written to `agent_runs/<timestamp>-<prefix>/patches/`.
Evaluation results are written to `agent_runs/hydra/<invocation>-evaluate/evaluation_output/`.

## Layout

```text
exp_agent/
├── README.md
├── agents/
│   ├── codex.sh       # Codex CLI adapter
│   ├── opencode.sh    # OpenCode adapter
│   └── traecli.sh     # TraeCLI / TraeX adapter
├── patch_agent_runner.py
├── process_data.py
├── run_infer.sh
└── run_eval.sh
```

## Data and images

By default, scripts use:

```text
../datasets/patcheval_verified.json
```

`patcheval_verified.json` provides CVE metadata, prompt content, and the `image_url` used for agent execution and evaluation. `../../scripts/images.txt` contains the same 230 image references for bulk download.

## Step 1: Generate patches

Use the unified inference entrypoint:

```bash
bash run_infer.sh <agent> <prefix>
```

Supported agents:

```text
codex
opencode
traecli
```

The generated run is written under:

```text
agent_runs/<timestamp>-<prefix>/
```

with patches in:

```text
agent_runs/<timestamp>-<prefix>/patches/CVE-....patch
```

Common controls:

```bash
export CONCURRENCY=5             # parallel containers / agents
export LIMIT=-1                  # -1 means all selected cases
```

### Codex

Required environment variable:

```bash
export CODEX_CONFIG=/path/to/codex-home/my-profile.config.toml
```

`CODEX_BIN` is optional and defaults to the pinned vendored release (see
[Pinned harness binaries](#pinned-harness-binaries)).

Run:

```bash
CONCURRENCY=5 bash run_infer.sh codex codex_my_profile
```

The Codex adapter mounts the Codex executable and config home into each case container. It derives the profile name from `CODEX_CONFIG` (`<profile>.config.toml`) and runs:

```text
codex exec --profile <profile> ... -C {workdir} < {prompt_file}
```

### OpenCode

Required environment variable:

```bash
export OPENCODE_CONFIG=/path/to/opencode-home/config/opencode/opencode.json
```

`OPENCODE_BIN` is optional and defaults to the pinned vendored release (see
[Pinned harness binaries](#pinned-harness-binaries)).

Run:

```bash
CONCURRENCY=5 bash run_infer.sh opencode opencode_default
```

The OpenCode adapter mounts the OpenCode executable, mounts the config home
read-only, and copies the data home into `/tmp/opencode-data` inside each case
container before running:

```text
opencode run --format json --auto < {prompt_file}
```

### TraeCLI / TraeX

Required environment variables:

```bash
export TRAE_BIN=/path/to/bin/traex
export TRAE_CONFIG=/path/to/trae-home/my-profile.traecli.toml
```

Run:

```bash
CONCURRENCY=5 bash run_infer.sh traecli trae_my_profile
```

The TraeCLI adapter mounts the `traex` executable and the directory containing
`TRAE_CONFIG`. It derives the profile name from `TRAE_CONFIG`
(`<profile>.traecli.toml`) and copies the Trae home into `/tmp` inside each case
container before running:

```text
traex exec --profile <profile> ... < {prompt_file}
```

For example:

```bash
export TRAE_BIN=/path/to/traecli-runtime/bin/traex
export TRAE_CONFIG=/path/to/trae-home/gpt54-gggso.traecli.toml
```

## Step 2: Evaluate patches

After patch generation, run:

```bash
bash run_eval.sh <prefix>
```

Example:

```bash
bash run_eval.sh codex_my_profile
```

`run_eval.sh` will:

1. find the latest completed matching run under `agent_runs/`, including Hydra runs;
2. convert `patches/*.patch` to PatchEval evaluation JSONL with `process_data.py`;
3. call `../evaluation/run_evaluation.py`.

The converted patch file is written to:

```text
agent_runs/hydra/<invocation>-evaluate/eval_inputs/patches.jsonl
```

Evaluation results are written by `run_evaluation.py` under:

```text
agent_runs/hydra/<invocation>-evaluate/evaluation_output/
```

You can also pass the run directory explicitly:

```bash
bash run_eval.sh <prefix> /path/to/agent_runs/<timestamp>-<prefix>
```

All workflow artifacts default to `patcheval/exp_agent/agent_runs/` (relative to
the repository root): patches, raw trajectories, normalized analysis, converted
evaluation input, reports, logs, harness configs, and resolved Hydra configs.
Hydra creates an isolated invocation directory under `agent_runs/hydra/`; its
`generation/`, `analysis/`, `eval_inputs/`, `evaluation_output/`, and `harnesses/`
children hold the corresponding artifacts. Repeated labels do not overwrite
previous default outputs. Direct generation uses a unique timestamp/random
directory ending in the run label.

Override `paths.runs` for a different artifact root, or use `hydra.run.dir`,
`hydra.sweep.dir`, `analysis.output_dir`, or `configure.output_dir` for explicit
destinations. Relative paths resolve from the repository root, even when the
launcher is called elsewhere. The temporary helper uses `RUNS_DIR` for the
same override. The legacy evaluation wrapper accepts `EVALUATION_OUTPUT_DIR`
for an explicit invocation directory and uses Hydra for isolated output.
Existing results are left in place; supply their path explicitly to evaluate
or analyze them.

Model caches and serving environments remain separate under `paths.runtime`
(default `~/.cache/patcheval/local_llm`, overridden by `RUNTIME_DIR`); `HF_HOME`
still controls the model cache. The temporary helper keeps its host-specific
runtime default `/mnt/local/patcheval-local-llm`.

## Runner behavior

`patch_agent_runner.py` is patch-generation-only. It does **not** run evaluation.

For each sample it:

1. reads the CVE image from the sample's `image_url` field;
2. starts a Docker container;
3. selects the repository workdir from `/workspace/<repo basename>`, then `/workspace/<repo basename lower-case>`, then `/workspace`;
4. hides non-repository files under `/workspace` when a repository subdirectory is selected;
5. writes a prompt to `/results/prompt.txt`;
6. runs the selected agent command;
7. collects `/workspace/fix.patch` or `git diff HEAD -U3`;
8. writes patches, logs, and `results.jsonl`.

## Output files

A generation run contains:

```text
agent_runs/<timestamp>-<prefix>/
├── patches/           # CVE-keyed patches for process_data.py
├── .work/             # prompt and agent stdout/stderr
├── results.jsonl
├── run_metadata.json
└── summary.json
```

## Notes

- `patch_url` metadata should not be used by agents as a repair source.
- During patch generation, the CLI prints case-level progress when a case
  finishes. Detailed agent logs are written under `.work/<case>/agent_stdout.txt`
  and `.work/<case>/agent_stderr.txt`.
- `run_infer.sh` exits with a non-zero status if any selected case fails, while
  preserving all generated patches and logs. Failed cases are represented by
  empty patch files and are counted as failed repairs during evaluation.
- Keep credentials and runtime homes outside version control.

### Pinned harness binaries

For comparable results, every generation path defaults to the same vendored
releases: Codex 0.155.0 and OpenCode 1.18.31 under `third_party/` (xz archives
with `SHA256SUMS`). The Hydra harness YAMLs and the `run_infer.sh` adapters
share these pins. On first use the executable is extracted beside its archive
(git-ignored); an extracted vendored binary is checksum-verified on every run.
Before any case container starts, the executable's `--version` must match the
pin, so a self-updating host install cannot silently change the agent.

`CODEX_BIN`/`OPENCODE_BIN` (or Hydra `harness.binary`) select another
executable. A different release also needs `CODEX_VERSION`/`OPENCODE_VERSION`
(Hydra: `harness.version`) set to its version; an empty value skips the check
and prints a warning. Each Hydra generation records the version it used in
`harness-version.json`.

```bash
HARNESS=opencode bash temp_run_script.sh smoke
# Explicit executable override (must still report the pinned version):
OPENCODE_BIN="$HOME/.opencode/bin/opencode" HARNESS=opencode bash temp_run_script.sh smoke
```
