# Repository Guidelines

## Project Structure & Module Organization

PatchEval evaluates agent-generated vulnerability repairs in Docker environments.

- `patcheval/evaluation/`: patch evaluator, shared utilities, and example input.
- `patcheval/exp_agent/`: patch-generation runner, conversion scripts, and CLI adapters in `agents/`.
- `patcheval/datasets/patcheval_verified.json`: metadata for 230 verified CVE cases.
- `scripts/`: Docker image downloader and `images.txt` manifest.
- `tests/`: Python unit tests; `docs/` contains submission instructions and figures.

Keep generation and evaluation separate. Generated artifacts default to `patcheval/exp_agent/agent_runs/`, including nested `eval_inputs/` and `evaluation_output/`, not alongside source files.

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
and `requirements.txt`. vLLM stays in a separate uv-managed environment.
Generation and evaluation remain separate actions; the underlying adapters,
runner, dataset, and evaluator retain their existing interfaces.

The shared configuration is `scripts/conf/config.yaml`. Config groups live in
`model/`, `harness/`, and `experiment/` beneath that directory. The supplied
model is `qwen3_8_27b`, harnesses are `codex`, `opencode`, and `traecli`, and
experiment presets are `smoke` (the first five cases, 8 generation containers
and 8 evaluation workers) and `full` (all cases, 48 generation containers and 16
evaluation workers).
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
when the CLI is launched from another directory. The entrypoint anchors execution at the repository root before Hydra initializes;
Hydra itself does not change directories. Explicit relative `hydra.run.dir` and
`hydra.sweep.dir` overrides therefore also resolve from the repository root. `paths.runs` defaults to `patcheval/exp_agent/agent_runs/`.
Each invocation records its config and overrides in
`paths.runs/<group>/<timestamp>-<name>/.hydra/` and its resolved settings in
`resolved.yaml`. Results are grouped by what was evaluated (`run_group` resolver
in `scripts/run.py`): `<group>` is `<model>_<Harness>_Max-output-token=<cap>`, e.g.
`Qwen3.8-27B_Codex_Max-output-token=16k` (model = basename of `model.served_name`,
cap from `model.output_tokens`: 16000 -> `16k`, 8192 -> `8k`), holding that
combination's generations, checks, evaluations, resumes, and merged pass@k reports.
Evaluations and resumes join the group of the run they act on (its folder under
`paths.runs`, or else the group recorded in its `resolved.yaml`), whatever
`harness` says; `serve`/`setup` go to `<model>_vLLM-serving/`. Cross-harness
comparisons are kept in `<model>_<A>-vs-<B>_Max-output-token=<cap>/`. The `run_name`
resolver names the folder after its work: `generate-<harness>-<label>`,
`resume-<resumed folder>`, `evaluate-<scored generation folder>` (or
`evaluate-<label>`), `check-<harness>`, `configure-<harness>`, `analyze-<harness>`,
`serve`, `setup`. `temp_run_script.sh smoke|full` lets Hydra choose that directory
and evaluates the run by its unique label. Hydra's override grammar rejects an
unquoted `=` inside a value, so `scripts/run.py` quotes `key=value` arguments whose
value contains `=` (not lists, dicts, or already-quoted values) before Hydra parses
them; `evaluation.run_dir=.../Max-output-token=16k/...` works unquoted.
Existing runs were regrouped on 2026-09-23 (earlier `hydra/` layout; label lookup
still finds legacy `hydra/` runs). `paths.runs/INDEX.md` describes each folder and
`paths.runs/RENAMES.tsv` maps every original name to its current location.
Redundant invocations are staged in `paths.runs/_to_delete/` with a
`DELETE_LIST.md` of reasons. Generation writes its run beneath that invocation's
`generation/` directory, keeping sweep jobs isolated. `--multirun` uses
`paths.runs/multirun/`. Resolved snapshots may contain values obtained
from environment interpolations, so keep credentials out of config files and
CLI overrides. Logs stay with their invocation. Serving environments and model
caches remain separate: `paths.runtime` defaults to `~/.cache/patcheval/local_llm`
(or `RUNTIME_DIR`), while `HF_HOME` overrides the model cache. All these
artifacts remain outside version control. `dry_run=true` saves configuration and prints the intended operation
without installing, serving, rendering harness homes, or running the benchmark.
Endpoint auto-discovery still requires Docker unless an explicit address is set.

### Local vLLM inference on H200

The default `model=qwen3_8_27b` serves `Qwen/Qwen3.8-27B` in BF16 with
`--tensor-parallel-size 1` and `--data-parallel-size 8` on eight H200s (the 27B checkpoint fits on one
GPU, so data parallel replicas raise throughput instead of tensor-sharding the
weights), a 262,144-token context, `server.memory_fraction=0.85`, and up to 8
running requests per replica (`server.max_running_requests=8`, `--max-num-seqs 8`).
vLLM balances requests across replicas (least-loaded, without per-conversation
affinity) behind one endpoint. Automatic tool choice is enabled.

Serving enables only lossless accelerations, all BF16 (no FP8 weights or KV):
MTP speculative decoding with the checkpoint's own draft layer
(`server.speculative={method: mtp, num_speculative_tokens: 3}`, from the vLLM
Qwen3.8 recipe; rejection sampling keeps the output distribution), batching,
prefix caching (`server.prefix_caching`; Mamba "align" mode for the hybrid
layers), chunked prefill (`server.chunked_prefill`), and vLLM's default CUDA
graphs (FULL_AND_PIECEWISE). vLLM 0.29.0 builds this configuration offline:
draft `Qwen3_5MTP`, `max_num_seqs=8`, and 2,048 batched tokens per step.
Sizing: only 16 of 64 layers use full attention (4 KV heads x 256 dims), so the
BF16 KV cache costs 64 KiB/token, about 900k tokens per H200 after 54 GB of
weights. The first 16k-cap OpenCode run peaked at ~72k context per session
(median) and ~135k (p90), so 8 slots per replica fit typical load and vLLM
preempts rather than fails at the extremes; `full` runs 48 containers (about 6
sessions per GPU, since agents spend part of each turn in tools). These values
are computed, not yet measured: confirm with the server's startup KV capacity,
MTP acceptance in its metrics log, and a short concurrency sweep before long
runs. Batching slows each request, so the fixed agent timeout binds sooner than
at one request per replica; compare timeout counts when changing concurrency.
Set these through the named fields; the corresponding flags in
`server.extra_args` are rejected. Generation warns when `generation.concurrency`
exceeds `data_parallel x max_running_requests`.

`serve` also writes `${paths.runtime}/current-server.json` (argv, vLLM version,
start time). Each generation copies it to `server.json` in its invocation, and
`generation.resume_dir` refuses to resume if the current record differs from
the invocation's, so all samples of a run share one engine configuration.
Restart the server to apply new serving settings, never during a generation
run; runs made before this record existed only produce a warning.
Thinking and sampling use the checkpoint defaults: `model.temperature=null`
sends no temperature from the server or any harness, so vLLM applies the
model's `generation_config.json` (its startup log reports the defaults).
Serving passes `--override-generation-config '{"max_new_tokens": 16000}'` from
`model.output_tokens`; in vLLM 0.29.0 `max_new_tokens` is a server-wide hard cap
on Chat Completions and Responses requests (min of the request's own limit, the
cap, and remaining context). Rendered OpenCode configs also set `limit.output`
to the cap. Codex has no official output-cap or temperature key; Codex 0.155.0
and 0.155.1 were observed to send neither, and OpenCode 1.18.31 sends no
temperature for custom models unless configured, so every harness gets the
model default. Setting `model.temperature` to a number instead adds it to the
server override (the default for requests that omit one: Codex, TraeCLI) and
renders OpenCode's [agent temperature](https://opencode.ai/docs/agents/#temperature)
on every built-in agent of OpenCode 1.18.31 (build, plan, general, explore,
compaction, summary, title) together with the model's `"temperature": true`
capability flag, without which 1.18.31 silently drops agent temperatures.
Newer OpenCode docs mention a `scout` agent and a 0.55 Qwen default; neither
applies to 1.18.31, so do not copy newer-doc agent names into these configs.
Set generation settings through these model fields;
`--override-generation-config`/`--generation-config` in `server.extra_args` are
rejected. Restart a running server after changing them. The `qwen3` reasoning and `qwen3_xml`
tool parsers are enabled. vLLM is pinned to 0.29.0; setup uses managed Python 3.12 with the development headers required
by Triton. Serving needs a C compiler and a compatible CUDA toolkit; initial
kernel compilation can take minutes. See the
[vLLM Qwen3.8 recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

Choose storage with room for approximately 54 GB of weights, the environment,
and caches. Keep these settings in an experiment YAML or use overrides:

```bash
bash scripts/run.sh action=setup paths.runtime=/mnt/local/patcheval-local-llm
bash scripts/run.sh action=serve paths.runtime=/mnt/local/patcheval-local-llm server.host=172.17.0.1
# In another terminal:
bash scripts/run.sh action=check server.host=172.17.0.1
```

Setup creates `${paths.runtime}/venv-vllm`, leaving the previous SGLang `venv`
untouched and reusing the configured Hugging Face cache. The explicit host above
avoids Docker socket access during serving; generation still requires Docker.
New harness profiles use provider ID `vllm`; regenerate reusable profiles or
update custom profiles yourself. `serve_sglang.sh` remains a deprecated forwarding
alias to `serve_vllm.sh`.

The migration is validated with unit tests and dry runs only. Live startup,
eight-replica GPU memory checks, streaming protocol checks, and a one-case
benchmark smoke test remain pending until a server is explicitly started.

`serve` stays in the foreground and forwards signals by replacing the launcher
with vLLM. It records the installed serving version in `server-version.json`
next to the resolved configuration. Redirect console output beneath `paths.runs`
when a separate console log is needed. `server.host=null` discovers the Docker bridge gateway; port
30000 is the default. Container localhost is not the host. For a different
endpoint set `server.host`, `server.port`, or `server.base_url` (client URL,
including `/v1`). `check.protocol=both|chat|responses` and `check.timeout=300`
control the streaming tool-call/result probes. Codex uses Responses and
OpenCode uses Chat Completions; no proxy is installed. `VLLM_API_KEY` is the optional
credential for serving and checks; checks accept `SGLANG_API_KEY` as a deprecated
fallback, with `VLLM_API_KEY` taking precedence. Configure matching credentials
in custom harness profiles when authentication is enabled.

Model path/name, context length, per-response output cap, sampling temperature, and parsers live in the model YAML.
GPU selection, tensor/data parallelism, dtype, memory, and concurrency live in
`server`. Extra serving arguments are literal list entries, for example
`'server.extra_args=[--enforce-eager]'`. Prefer named configuration
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
`generation.timeout`, `generation.samples`, `evaluation.max_workers`, and
`evaluation.log_level` in YAML or as CLI overrides.

`generation.samples=4` (the default) runs four independent generations of every
case, one after another, into `generation/sample_<i>/` of the same invocation.
The runner exits 1 whenever any task fails; a sample whose run still completed
(`summary.json` written) counts as complete and the next sample starts, and
only samples without a completed run are reported as failed at the end.
Evaluation accepts that invocation, its `generation/` directory, a `sample_<i>`
directory, or any sample's run directory as `evaluation.run_dir` (all expand to
every sample of the invocation), or finds it by `label`. It refuses to score
unless every configured sample completed; the error names the missing samples
and the resume command. `evaluation.allow_partial=true` (`PARTIAL=1` in
`temp_run_script.sh evaluate`) scores only the completed samples and marks
`pass_at_k.json` as `partial`. `generation.resume_dir=<invocation>` (or
`bash temp_run_script.sh resume <invocation>`) generates only the missing
samples into that invocation: it adopts the invocation's recorded model,
harness, label, endpoint, dataset, and generation settings, reuses its rendered
`harnesses/` configs, refuses to continue if the harness CLI version differs
from the recorded `harness-version.json`, and appends to `resumed_by.jsonl`.
Resume also reruns, in place, every task of a completed sample whose agent never
started (see the startup watchdog below), then generates missing samples; it
refuses to touch a sample whose unfinished run was written in the last 15
minutes, since that run is probably still generating. Each sample is converted and evaluated into
`eval_inputs/sample_<i>/` and `evaluation_output/sample_<i>/`, and
`pass_at_k.json` reports pass@1 through pass@samples overall and per language,
using the unbiased estimator 1 - C(n-c,k)/C(n,k); pass@1 is the mean
single-sample solve rate over all samples, and pass@4 with four samples is the
fraction of cases solved at least once. Per-sample solve counts and evaluator
execution errors are included for inspection. Each pass@k (overall and per language) also has an
`uncertainty` block: `stderr`, `variance`, and `ci95` over CVEs of the per-CVE
unbiased estimates (normal 95% CI, clipped to [0, 1]), and run-to-run spread
`run_values`/`run_variance`/`run_std`/`run_min`/`run_max` from pass@k of every
size-k subset of the sample runs (pass@1: each run; pass@n: one subset, variance
null). `per_cve_solved_samples` and `per_cve_language` are stored for later
comparisons. `python -m scripts.infer.pass_at_k --compare A/pass_at_k.json
B/pass_at_k.json --labels a,b --out DIR` writes `comparison.json`, `comparison.md`
and `error_bars.svg`: paired B - A differences over the same CVEs with SE, 95% CI,
a two-sided sign-flip permutation p-value (headline; 20,000 draws, seed 0) and a
normal-approximation p-value, overall and per language. At high k the per-CVE
differences are mostly -1/0/+1, so prefer the permutation p. Standard library only. To pool samples from several
evaluations (for example two runs of the same harness), use
`python -m scripts.infer.pass_at_k --eval EVAL_DIR[:sample_0,sample_1] --eval ... --out NEW_DIR`;
all samples must cover the same CVEs, and the report lists each sample's
evaluation, generation run, `server.json`, and harness version and sets
`mixed_serving_settings`/`mixed_harness_versions` when they differ.
`generation.samples=1` keeps the
previous single-run layout; legacy single runs are evaluated as before, with a
one-sample `pass_at_k.json`. Evaluation never implicitly starts generation. Each evaluation invocation
stores conversion input in its own `eval_inputs/` directory and reports/logs in
its own `evaluation_output/` directory; repeated labels do not overwrite prior
reports. The conversion and evaluator Python entry points are unchanged.

Standalone `action=configure` writes harness configs to its invocation's
`harnesses/` directory by default. `configure.output_dir` and
`analysis.output_dir` accept explicit destinations, resolved from the repository
root when relative. Analysis defaults to its own invocation's `analysis/`.

`temp_run_script.sh` uses the same default artifact root; `RUNS_DIR` overrides
it, with relative values resolved from the repository root. Its `RUNTIME_DIR`
continues to default to `/mnt/local/patcheval-local-llm` on this host. Existing
results under `/mnt/local/patcheval-runs` are not moved or deleted; pass their
absolute generation path to evaluate or analyze them.

The legacy `run_eval.sh` now delegates to Hydra, retaining its positional prefix
and optional run directory plus `MAX_WORKERS`, `LOG_LEVEL`, and `DATASET`.
`RUNS_DIR` (or `OUTPUT_BASE`) selects its artifact root; `EVALUATION_OUTPUT_DIR`
explicitly selects the evaluation invocation directory. Legacy `run_infer.sh`
retains `OUTPUT_BASE`, with `RUNS_DIR` as a fallback. The direct Python runner
also defaults to the shared artifact root and creates a unique directory on
each call, including calls made within the same second.

To render reusable configs separately:

```bash
bash scripts/run.sh action=configure configure.output_dir=/path/to/harnesses
```

Existing generated files are protected unless `configure.force=true`. The
Codex home contains `config.toml` and `local.config.toml`; Codex 0.154.0 and
later load named profiles from the latter, so do not add legacy `[profiles.local]` tables.
OpenCode receives its expected XDG config/data layout. The integration was
checked with Codex 0.154.0, 0.155.0, and 0.155.1 and OpenCode 1.18.31. Both
harnesses are pinned so every run uses the same agent: the official release
binaries are vendored under `third_party/` with their licenses, `SHA256SUMS`, and
a provenance README, as xz archives stored with Git LFS
(`third_party/**/*.xz`; install `git-lfs` and run `git lfs install` before
cloning or pushing):

- Codex 0.155.0: `third_party/codex/0.155.0/codex-x86_64-unknown-linux-musl.xz`
  (static musl build, Apache-2.0), identical to the `rust-v0.155.0` asset.
- OpenCode 1.18.31: `third_party/opencode/1.18.31/opencode-linux-x64.xz`
  (glibc build, MIT), identical to the `v1.18.31` `opencode-linux-x64.tar.gz`.

`scripts/conf/harness/{codex,opencode}.yaml` default `harness.binary` to the
extracted executable beside each archive and set `harness.version`. Generation
extracts a missing executable on first use, verifies both checksums, and keeps
it git-ignored; it then refuses any binary whose `--version` differs from
`harness.version` (the hosts' standalone installs update themselves, e.g. Codex
to 0.156.0). An extracted vendored binary is checksum-verified on every run.
`CODEX_BIN`/`OPENCODE_BIN` or `harness.binary` still select another
executable, which also requires overriding `harness.version` (or `null` to skip
the check). The legacy `run_infer.sh` adapters enforce the same pins through
`patcheval/exp_agent/pinned_harness.sh`: with `CODEX_BIN`/`OPENCODE_BIN` unset
they use the vendored binary, and before any case starts they require
`--version` to match `CODEX_VERSION`/`OPENCODE_VERSION` (default: the pin in
`agents/<harness>.sh`, kept equal to the YAML by `tests/test_pinned_harness.py`;
empty skips the check). Hydra passes `harness.version` through to them. Each generation records the CLI version in `harness-version.json`
beside `resolved.yaml`; with `harness.version=null`, a rendered OpenCode config
used with a release other than 1.18.31 only warns. To sweep generation settings, use Hydra `--multirun`, e.g.
`bash scripts/run.sh --multirun action=generate experiment=smoke harness=codex,opencode`.
The default Hydra launcher runs sweep jobs sequentially; avoid serving sweeps
on a shared GPU/port.

The existing script names are thin Hydra aliases:

```bash
bash scripts/infer/serve_vllm.sh serve server.gpu=0 server.data_parallel=1
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
Change `generation.concurrency` together with `server.data_parallel` and
`server.max_running_requests`, and only after checking KV capacity and timeouts. The existing
end-to-end verified dataset is unchanged; no new partition is introduced. An
unsuccessful repair is not an infrastructure failure. Keep reference patches
and fix metadata out of repair prompts and tools.

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

Startup watchdog: at high concurrency some OpenCode 1.18.31 sessions hang
right after "initialized" (no model request, no sockets, near-zero CPU) and would
otherwise burn the whole agent timeout as an empty-patch failure. Each adapter
declares `AGENT_READY_PATTERN`, printed once the app has started (`"type":"step_start"`
for OpenCode, `"type":"turn.started"` for Codex; TraeCLI has none, so no watchdog).
If it does not appear within `STARTUP_TIMEOUT` seconds (default 300), or the agent
exits first, the runner removes the container and retries in a fresh one, up to
`STARTUP_RETRIES` times (default 2). No model output exists at that point, so a
retry cannot bias results; the agent timeout applies only to agents that started.
Results record `startup_attempts` and `startup_failed`; trajectory metadata lists
each stall (`startup_stalls`), and stalled logs stay under
`.work/<task>/startup_attempt_<n>/`. For runs made before the watchdog, resume
recognises such tasks as agent failures whose archived stdout is missing or
lacks the ready marker; a missing stream also recovers a rerun that was
interrupted after moving old artifacts aside but before merging results. Reruns replace the task's row, patch, and trajectory in the original run
directory (replaced artifacts move to `startup_reruns/<time>/replaced/`, and
`startup_reruns.jsonl` records each rerun) and recompute `summary.json`.

The runner defaults to `AGENT_TIMEOUT=2400` seconds (40 minutes; Hydra
`generation.timeout=2400`). A timed-out agent
produces an empty submitted patch, even if it changed its working tree before
timeout, because patch collection requires a successful agent exit. Choose a
time budget suitable for local generation speed when measuring repair success.

The pinned vendored OpenCode is the default. With `harness.binary=opencode`
(an unpinned bare name, which also needs `harness.version`), executable discovery
checks `PATH` first, then `~/.opencode/bin/opencode`. This supports zsh and
noninteractive Bash without sourcing `.bashrc`. An explicit `OPENCODE_BIN` or
`harness.binary` path takes precedence; invalid explicit paths fail rather than
silently selecting a different installation. For the temporary helper:

```bash
HARNESS=opencode bash temp_run_script.sh smoke
# Optional explicit executable override:
OPENCODE_BIN="$HOME/.opencode/bin/opencode" HARNESS=opencode bash temp_run_script.sh smoke
```
