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

## Hydra workflow configuration

Use `bash scripts/run.sh` (or `uv run python scripts/run.py`) for the new
workflow commands. `hydra-core` is included in both `pyproject.toml`/`uv.lock`
and `requirements.txt`. SGLang stays in a separate uv-managed environment.
Generation and evaluation remain separate actions; the underlying adapters,
runner, dataset, and evaluator retain their existing interfaces.

The shared configuration is `scripts/conf/config.yaml`. Config groups live in
`model/`, `harness/`, and `experiment/` beneath that directory. The supplied
model is `qwen3_8_27b`, harnesses are `codex`, `opencode`, and `traecli`, and
experiment presets are `smoke` (one case) and `full` (all cases). Both presets
start with one concurrent generation container and one evaluation worker.
Add another YAML file to the appropriate group to save reusable settings.
Experiment files use `# @package _global_` to override shared settings; they
may select groups using `defaults: [{override /harness: opencode}]`.

Hydra composition, command-line overrides, and config inspection are documented
at [Hydra Defaults List](https://hydra.cc/docs/1.3/advanced/defaults_list/).
Inspect configuration before executing; inspection does not contact Docker:

```bash
bash scripts/run.sh --cfg job --resolve experiment=smoke harness=opencode
bash scripts/run.sh action=serve dry_run=true server.host=127.0.0.1
```

All workflow-relative paths are resolved from the repository root, including
when the CLI is launched from another directory. Hydra does not change the
working directory. Each invocation records its config and overrides in
`paths.runs/hydra/<timestamp>-<action>/.hydra/` and its resolved settings in
`resolved.yaml`. Generation writes its run beneath that invocation's
`generation/` directory, keeping sweep jobs isolated. `--multirun` uses
`paths.runs/hydra/multirun/`. Resolved snapshots may contain values obtained
from environment interpolations, so keep credentials out of config files and
CLI overrides. Runtime homes, model caches, and logs remain outside version
control. `dry_run=true` saves configuration and prints the intended operation
without installing, serving, rendering harness homes, or running the benchmark.
Endpoint auto-discovery still requires Docker unless an explicit address is set.

### Local SGLang inference on H200

The default `model=qwen3_8_27b` serves `Qwen/Qwen3.8-27B` in BF16 with one GPU,
a 65,536-token context, `server.memory_fraction=0.85`, and one active request.
Thinking uses the checkpoint defaults. The `qwen3` reasoning and `qwen3_coder`
tool parsers are enabled. SGLang is pinned to 0.5.19; setup allows its prerelease
dependencies and uses managed Python 3.12 with the development headers required
by Triton. Serving needs a C compiler and a compatible CUDA toolkit; initial
kernel compilation can take minutes. See the
[SGLang Qwen3.8 recipe](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B).

Choose storage with room for approximately 54 GB of weights, the environment,
and caches. Keep these settings in an experiment YAML or use overrides:

```bash
bash scripts/run.sh action=setup paths.runtime=/mnt/local/patcheval-local-llm
bash scripts/run.sh action=serve paths.runtime=/mnt/local/patcheval-local-llm
# In another terminal:
bash scripts/run.sh action=check
```

`serve` stays in the foreground and forwards signals by replacing the launcher
with SGLang. It records the installed serving version in `server-version.json`
next to the resolved configuration. Redirect console output to a runtime log
when needed. `server.host=null` discovers the Docker bridge gateway; port
30000 is the default. Container localhost is not the host. For a different
endpoint set `server.host`, `server.port`, or `server.base_url` (client URL,
including `/v1`). `check.protocol=both|chat|responses` and `check.timeout=300`
control the streaming tool-call/result probes. Codex uses Responses and
OpenCode uses Chat Completions; no proxy is installed. `SGLANG_API_KEY` remains
the optional check credential environment variable.

Model path/name, context/output limits, and parsers live in the model YAML.
GPU selection, tensor parallelism, dtype, memory, and concurrency live in
`server`. Extra serving arguments are literal list entries, for example
`'server.extra_args=[--attention-backend,fa3]'`. Prefer named configuration
fields for settings already exposed so the server and harnesses stay aligned.
Other model families may need different parsers. The service is unauthenticated
by default; keep it on the local bridge or configure authentication in both the
server and supplied harness configs.

### Saving per-task trajectories

`generation.save_trajectories=true` is enabled in `scripts/conf/config.yaml`.
Override with `generation.save_trajectories=false` to disable the analysis
archives. The underlying Bash runner also accepts `SAVE_TRAJECTORIES=true`,
and the Python runner accepts `--save-trajectories` (off by default when used
directly).

Each generation run saves `trajectories/<index>-patcheval_<CVE>/` containing
`prompt.txt`, `stdout.jsonl`, `stderr.txt`, and `metadata.json`. `stdout.jsonl`
is the unmodified harness JSON event stream: model messages, tool calls/results,
and any reasoning events the harness emits. No events are filtered or truncated;
custom non-JSON agent commands retain their raw output as well. This captures
what the harness exposes, not model internals unavailable through its interface.
The CLI streams are continuously written to `.work/<task>/agent_stdout.txt`
and `agent_stderr.txt`, then archived when the task ends, including nonzero
exits and timeouts. `results.jsonl` links each task via `trajectory_path`.
Metadata includes the task outcome, exit code, timeout, agent duration, native
capture status, and the original work-log path if archival encounters an error.

Before deleting a case container, trajectory capture stops its writers and
copies available native session records: Codex `sessions/`, and OpenCode's
SQLite database plus WAL/SHM files (or legacy `storage/`). Keep SQLite files
together when opening an archived database; use a working copy for analysis.
Only these session paths are exported, never entire credential-bearing homes.
TraeCLI retains its JSON output stream; no native session export is configured.
Missing optional native files are recorded in metadata. These artifacts live
under ignored generation outputs. Disabling archives retains the existing
`.work` prompt/stdout/stderr diagnostics, including partial timeout output.

```bash
bash scripts/run.sh action=generate experiment=smoke harness=codex \
  generation.save_trajectories=true
```

### Normalized trajectory analysis

Run a separate, read-only analysis of completed Codex archives:

```bash
bash scripts/run.sh action=analyze analysis.input_dir=/absolute/path/to/generation-run
```

The input may also be a benchmark folder or one archived task. Results default
to the analysis invocation's `analysis/` directory. Set `analysis.output_dir`
to a new directory to choose another destination; existing output directories
and raw trajectory directories are protected. Running tasks are skipped; rerun
this action for a fresh snapshot after more tasks finish. This action does not
contact Docker or change generation, evaluation, or raw logs.

Each task JSON groups native events by session and item ID, and tools by
`call_id`. It retains exact item variants and source file/line references.
Reasoning uses content (or raw content) preferentially, with summary as a
fallback; these aliases are never concatenated. User-message UI events with
separate IDs are correlated only against a unique exact message within the
same session and turn, with the alias basis recorded. Other distinct IDs are
not merged just because their text matches. CLI JSON is referenced rather than
added to native counts because its IDs need not match native IDs.

Token totals sum `token_usage_record.usage` once per session/response ID.
`turn_token_usage`, `thread_token_usage`, and `token_count` mirrors are excluded.
Reasoning tokens are reported as a subset of output tokens, never added again.
Conflicting usage records and missing IDs produce warnings; cumulative-only
archives are not assigned fabricated per-response usage.

Tool records preserve `CommandExecution.stdout`, `stderr`, `aggregated_output`,
and exact `function_call_output` variants separately. Comparisons identify
wrapper-only differences, containment, other differences, and truncation
markers. Do not assume command stdout is the complete model-visible response:
in the first 14 Qwen task archives, 677 comparisons differed only by wrappers
and 67 had content differences, including 14 with truncation markers. Some
function outputs contain text absent from both stdout and aggregated output,
even with empty stderr. These observations do not establish the harness's
internal cause, and text already omitted by the harness cannot be recovered.
Source hashes in each normalized JSON support checking archive integrity.

### Harness configuration, generation, and evaluation

Generation automatically renders fresh Codex/OpenCode configs in its Hydra
invocation directory, using the selected model and endpoint. `harness.binary`
selects the installed executable. Set `harness.config=/path/to/profile` to use
an existing config instead; `traecli` requires this field. Existing custom
configs are passed through rather than rewritten, and their model settings
must be kept in sync by the caller.

```bash
bash scripts/run.sh action=generate experiment=smoke harness=codex label=my_codex_smoke
bash scripts/run.sh action=generate experiment=smoke harness=opencode \
  harness.binary=/path/to/opencode label=my_opencode_smoke
bash scripts/run.sh action=evaluate label=my_codex_smoke \
  evaluation.run_dir=/absolute/path/to/printed-generation-run
```

When `evaluation.run_dir=null`, evaluation selects the newest completed run
matching `label` under `paths.runs`, including Hydra single runs, multiruns,
and the previous flat layout. For custom `hydra.run.dir` or `hydra.sweep.dir`
locations outside this layout, supply `evaluation.run_dir` explicitly. A run
with recorded failures is still completed and is included in evaluation.
Set `paths.dataset`, `generation.limit`, `generation.concurrency`,
`generation.timeout`, `evaluation.max_workers`, and `evaluation.log_level` in
YAML or as CLI overrides. Evaluation never implicitly starts generation. Each evaluation invocation
stores conversion input in its own `eval_inputs/` directory and reports/logs in
its own `evaluation_output/` directory; repeated labels do not overwrite prior
reports. The conversion and evaluator Python entry points are unchanged.

To render reusable configs separately:

```bash
bash scripts/run.sh action=configure configure.output_dir=/path/to/harnesses
```

Existing generated files are protected unless `configure.force=true`. The
Codex home contains `config.toml` and `local.config.toml`; Codex 0.154.0 loads
named profiles from the latter, so do not add legacy `[profiles.local]` tables.
OpenCode receives its expected XDG config/data layout. The integration was
checked with Codex 0.154.0 and OpenCode 1.18.31. Record CLI versions alongside
benchmark outputs. To sweep generation settings, use Hydra `--multirun`, e.g.
`bash scripts/run.sh --multirun action=generate experiment=smoke harness=codex,opencode`.
The default Hydra launcher runs sweep jobs sequentially; avoid serving sweeps
on a shared GPU/port.

The existing script names are thin Hydra aliases:

```bash
bash scripts/infer/serve_sglang.sh serve server.gpu=0
uv run python scripts/infer/configure_harnesses.py configure.force=true
uv run python scripts/infer/check_server.py check.protocol=responses
bash scripts/generate_patches.sh experiment=smoke harness=codex label=my_run
bash scripts/evaluate_patches.sh label=my_run evaluation.run_dir=/path/to/run
```

The new aliases use Hydra `key=value` overrides instead of the previous
positional harness/prefix and per-command environment variables. For example,
`LIMIT=1 CONCURRENCY=1 ... codex smoke` becomes
`... harness=codex experiment=smoke label=smoke`. `RUNTIME_DIR`, `HF_HOME`, and
`CODEX_BIN`/`OPENCODE_BIN`/`TRAE_BIN` remain optional YAML environment defaults;
explicit Hydra overrides take precedence. The lower-level
`patcheval/exp_agent/run_infer.sh` and `run_eval.sh` still accept their original
environment-based interfaces.

Use `experiment=full` and a distinct label after reviewing smoke outputs.
Increase `generation.concurrency` and `server.max_running_requests` together
only after measuring GPU memory. The existing end-to-end verified dataset is
unchanged; no new partition is introduced. An unsuccessful repair is not an
infrastructure failure. Keep reference patches and fix metadata out of repair
prompts and tools.

## Docker storage on the ephemeral disk

On this host, `../ephemeral` links to the mounted filesystem at `/mnt/local`.
Image names and the Docker socket remain unchanged, so the downloader, agent
runner, and evaluator talk to the same daemon; they do not choose where layers
are stored.

`{"data-root": "/mnt/local/docker"}` in `/etc/docker/daemon.json` is **not**
enough by itself. This engine uses the containerd snapshotter (`Storage Driver:
overlayfs`, `driver-type: io.containerd.snapshotter.v1`). Docker's data-root
then holds only daemon metadata (networks, volumes, image *names*). Image
blobs and overlay snapshots stay in containerd's own root, which defaults to
`/var/lib/containerd` while `root` is commented out in
`/etc/containerd/config.toml`.

That split filled the boot disk during a bulk pull: `/` hit 100% (226 GB) with
0 bytes free, `/mnt/local` stayed ~1% used, `/mnt/local/docker` was hundreds of
KB, and `/var/lib/containerd` held ~201 GB
(`io.containerd.content.v1.content` plus
`io.containerd.snapshotter.v1.overlayfs`). Further pulls then failed with
"no space left on device" even though the ephemeral disk had terabytes free.

Point **both** stores at the mounted disk before downloading:

```json
{"data-root": "/mnt/local/docker"}
```

```toml
root = "/mnt/local/containerd"
```

If images were already pulled into `/var/lib/containerd`, stop Docker and
containerd, rsync that tree to the new root, start the services, confirm
`docker images` still lists them, then remove the old directory. Creating
`/mnt/local/docker` or changing only `data-root` does not relocate containerd.
A Docker systemd override may also require the mount and check that
`/mnt/local` is a mount point before starting Docker. The existing `/etc/fstab`
entry mounts the disk at boot. These are host settings, not settings applied
automatically by cloning this repository.

Verify **both** filesystems and **both** roots before a bulk download:

```bash
readlink -f ../ephemeral
findmnt --target /mnt/local
docker info --format '{{.DockerRootDir}} {{.Driver}}'
df -h / /mnt/local
sudo du -sh /mnt/local/docker /var/lib/containerd /mnt/local/containerd
```

`DockerRootDir=/mnt/local/docker` is not sufficient. Confirm containerd's
content and overlay directories are on `/mnt/local`, not `/`, and that `/`
still has headroom. The downloader's "storage:" line reports Docker's
data-root only.

To keep download logs on that disk, run from the repository root:

```bash
python - <<'PYTHON'
from scripts.download_images import batch_pull_images

success, failed = batch_pull_images(
    images_file="scripts/images.txt",
    log_file="../ephemeral/patcheval-pull.log",
    max_workers=4,
)
raise SystemExit(0 if success > 0 and failed == 0 else 1)
PYTHON
```

`scripts/download_images.py` uses relative `images.txt` and `pull_images.log`,
so run it from `scripts/` (or pass those paths). It does not select the image
destination. On other hosts, configure Docker's data-root **and** containerd's
`root` for the desired mounted filesystem before downloading. The daemon also
needs socket access (`docker` group or equivalent); a missing SDK in system
Python is unrelated to disk layout.

The existing runner defaults to `AGENT_TIMEOUT=3600` seconds. A timed-out agent
produces an empty submitted patch, even if it changed its working tree before
timeout, because patch collection requires a successful agent exit. Choose a
time budget suitable for local generation speed when measuring repair success.
