#!/usr/bin/env python3
"""Hydra entry point for local serving and PatchEval generation/evaluation."""

import datetime
import hashlib
import json
import lzma
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.infer.check_server import run_checks
from scripts.infer.configure_harnesses import OPENCODE_VERSION, configure
from scripts.infer.pass_at_k import aggregate


def absolute(value):
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


OmegaConf.register_new_resolver("repo", lambda: str(ROOT), replace=True)
OmegaConf.register_new_resolver("absolute", lambda value: str(absolute(value)), replace=True)


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def validate(cfg):
    if cfg.action not in {"setup", "serve", "configure", "check", "generate", "evaluate", "analyze"}:
        raise ValueError("action must be setup, serve, configure, check, generate, evaluate, or analyze")
    if not isinstance(cfg.generation.save_trajectories, bool):
        raise ValueError("generation.save_trajectories must be a boolean")
    if cfg.harness.name not in {"codex", "opencode", "traecli"}:
        raise ValueError("Unknown harness")
    if not isinstance(cfg.label, str) or len(cfg.label) > 100 or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", cfg.label):
        raise ValueError("label must be <= 100 characters, start with a letter, digit, or underscore, and contain no path separators")
    for key in ("model.context_length", "model.output_tokens", "server.port", "server.tensor_parallel",
                "server.data_parallel", "server.max_running_requests", "generation.concurrency",
                "generation.timeout", "generation.samples", "evaluation.max_workers"):
        positive(OmegaConf.select(cfg, key), key)
    if cfg.model.output_tokens >= cfg.model.context_length:
        raise ValueError("model.output_tokens must be smaller than model.context_length")
    temperature = cfg.model.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                                    or temperature < 0):
        raise ValueError("model.temperature must be a non-negative number or null")
    if cfg.server.port > 65535 or not 0 < cfg.server.memory_fraction < 1:
        raise ValueError("Invalid server port or memory_fraction (must be between 0 and 1)")
    if not isinstance(cfg.generation.limit, int) or cfg.generation.limit == 0 or cfg.generation.limit < -1:
        raise ValueError("generation.limit must be -1 or a positive integer")
    if cfg.check.protocol not in {"both", "chat", "responses"} or cfg.check.timeout <= 0:
        raise ValueError("Invalid check.protocol or check.timeout")
    if not OmegaConf.is_list(cfg.server.extra_args) or not all(isinstance(arg, str) for arg in cfg.server.extra_args):
        raise ValueError("server.extra_args must be a list of strings")
    if any(arg.split("=", 1)[0] in {"--override-generation-config", "--generation-config"}
           for arg in cfg.server.extra_args):
        raise ValueError("Set model.output_tokens/model.temperature instead of generation-config extra_args")
    managed = {"--speculative-config", "-sc", "--enable-prefix-caching", "--no-enable-prefix-caching",
               "--enable-chunked-prefill", "--no-enable-chunked-prefill", "--max-num-seqs"}
    if any(arg.split("=", 1)[0] in managed for arg in cfg.server.extra_args):
        raise ValueError("Set server.speculative/prefix_caching/chunked_prefill/max_running_requests "
                         "instead of passing those flags in server.extra_args")
    for key in ("prefix_caching", "chunked_prefill"):
        if not isinstance(cfg.server.get(key, True), bool):
            raise ValueError(f"server.{key} must be a boolean")
    speculative = cfg.server.get("speculative")
    if speculative is not None:
        if not OmegaConf.is_dict(speculative) or not isinstance(speculative.get("method"), str):
            raise ValueError("server.speculative must be null or a mapping with a method")
        positive(speculative.get("num_speculative_tokens"), "server.speculative.num_speculative_tokens")
    slots = cfg.server.data_parallel * cfg.server.max_running_requests
    if cfg.action == "generate" and cfg.generation.concurrency > slots:
        print(f"WARNING: generation.concurrency={cfg.generation.concurrency} exceeds the server's "
              f"{slots} running-request slots; extra model calls will queue", file=sys.stderr)


def bridge_host():
    try:
        host = subprocess.check_output(
            ["docker", "network", "inspect", "bridge", "--format",
             "{{(index .IPAM.Config 0).Gateway}}"], text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Cannot discover Docker bridge; set server.host or server.base_url") from exc
    if not host or host == "<no value>":
        raise ValueError("Cannot discover Docker bridge; set server.host or server.base_url")
    return host


def bind_host(cfg):
    if cfg.server.host is None:
        cfg.server.host = bridge_host()
    return cfg.server.host


def endpoint(cfg):
    if cfg.server.base_url:
        return cfg.server.base_url.rstrip("/")
    host = bind_host(cfg)
    if host in {"0.0.0.0", "::"}:
        host = bridge_host()
    if ":" in host:
        host = f"[{host}]"
    cfg.server.base_url = f"http://{host}:{cfg.server.port}/v1"
    return cfg.server.base_url


def serving_environment(cfg):
    return {
        "HF_HOME": str(absolute(cfg.paths.hf_cache)),
        "UV_CACHE_DIR": str(absolute(cfg.paths.uv_cache)),
        "UV_PYTHON_INSTALL_DIR": str(absolute(cfg.paths.python_install)),
        "CUDA_VISIBLE_DEVICES": str(cfg.server.gpu),
    }


def serve_command(cfg):
    args = [str(absolute(cfg.paths.serving_env) / "bin/vllm"), "serve", cfg.model.path,
            "--served-model-name", cfg.model.served_name,
            "--host", bind_host(cfg), "--port", str(cfg.server.port),
            "--tensor-parallel-size", str(cfg.server.tensor_parallel),
            "--data-parallel-size", str(cfg.server.data_parallel), "--dtype", cfg.server.dtype,
            "--max-model-len", str(cfg.model.context_length),
            "--gpu-memory-utilization", str(cfg.server.memory_fraction),
            "--max-num-seqs", str(cfg.server.max_running_requests)]
    for key, flag in (("reasoning_parser", "--reasoning-parser"), ("tool_call_parser", "--tool-call-parser")):
        if cfg.model[key]:
            args.extend([flag, cfg.model[key]])
    if cfg.model.tool_call_parser:
        args.append("--enable-auto-tool-choice")
    # max_new_tokens is a server-wide hard cap for Chat and Responses requests;
    # temperature is the default for clients that do not send one (e.g. Codex).
    generation = {"max_new_tokens": cfg.model.output_tokens}
    if cfg.model.get("temperature") is not None:
        generation["temperature"] = cfg.model.temperature
    args.extend(["--override-generation-config", json.dumps(generation)])
    # Lossless accelerations, emitted explicitly so the recorded argv states them
    # even where they match vLLM's defaults.
    speculative = cfg.server.get("speculative")
    if speculative is not None:
        args.extend(["--speculative-config", json.dumps(OmegaConf.to_container(speculative, resolve=True))])
    for key, flag in (("prefix_caching", "prefix-caching"), ("chunked_prefill", "chunked-prefill")):
        if cfg.server.get(key) is not None:
            args.append(f"--{'' if cfg.server[key] else 'no-'}enable-{flag}")
    return args + list(cfg.server.extra_args)


def current_server_file(cfg):
    return absolute(cfg.paths.runtime) / "current-server.json"


def record_server(cfg, output):
    """Copy the last server started by this workflow into a generation invocation."""
    source = current_server_file(cfg)
    if not source.is_file():
        print(f"WARNING: {source} not found; the serving configuration is not recorded "
              "(start the server with action=serve to record it)", file=sys.stderr)
        return None
    record = json.loads(source.read_text())
    (output / "server.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def check_resume_server(target, record):
    stored = target / "server.json"
    if not stored.is_file() or record is None:
        print(f"WARNING: cannot confirm that {target} was generated with the current server "
              "configuration (no server record on one side)", file=sys.stderr)
        return
    previous = json.loads(stored.read_text())
    if (previous.get("argv"), previous.get("vllm")) != (record.get("argv"), record.get("vllm")):
        raise ValueError(f"The serving configuration differs from the one {target} was generated "
                         "with; restart the server with its recorded settings (server.json) to resume")


SAMPLE_DIR = re.compile(r"sample_(\d+)")


def completed(path):
    return path.is_dir() and (path / "summary.json").is_file() and (path / "patches").is_dir()


def configured_samples(generation):
    resolved = generation.parent / "resolved.yaml"
    if resolved.is_file():
        return OmegaConf.load(resolved).get("generation", {}).get("samples")
    return None


def sample_runs(generation, allow_partial=False):
    """Completed runs of a multi-sample generation directory, in sample order."""
    done = []
    for directory in sorted((d for d in generation.iterdir() if d.is_dir() and SAMPLE_DIR.fullmatch(d.name)),
                            key=lambda d: int(SAMPLE_DIR.fullmatch(d.name).group(1))):
        runs = [run for run in directory.iterdir() if completed(run)]
        if len(runs) > 1:
            raise ValueError(f"Expected one completed run in {directory}, found {len(runs)}")
        done.extend(runs)
    expected = configured_samples(generation)
    if expected is None:
        expected = max((int(SAMPLE_DIR.fullmatch(d.name).group(1)) + 1 for d in generation.iterdir()
                        if d.is_dir() and SAMPLE_DIR.fullmatch(d.name)), default=0)
    if len(done) != expected and not (allow_partial and done):
        names = ", ".join(run.parent.name for run in done) or "none"
        raise ValueError(
            f"{len(done)} of {expected} configured samples completed in {generation} ({names}). "
            f"Generate the missing samples with: bash temp_run_script.sh resume {generation.parent} "
            f"(or generation.resume_dir={generation.parent}); set evaluation.allow_partial=true to "
            "score only the completed samples.")
    return done


def find_runs(cfg):
    """Generation run(s) to evaluate: one legacy run or every sample of one invocation."""
    partial = bool(cfg.evaluation.get("allow_partial"))
    if cfg.evaluation.run_dir:
        path = absolute(cfg.evaluation.run_dir)
        if path.is_dir() and (path / "patches").is_dir():
            # A run inside generation/sample_<i>/ stands for its whole invocation.
            if SAMPLE_DIR.fullmatch(path.parent.name):
                return sample_runs(path.parent.parent, partial)
            return [path]
        if path.is_dir() and SAMPLE_DIR.fullmatch(path.name):
            return sample_runs(path.parent, partial)
        for generation in (path, path / "generation"):
            if generation.is_dir() and any(SAMPLE_DIR.fullmatch(d.name) for d in generation.iterdir()):
                return sample_runs(generation, partial)
        raise ValueError(f"Not a generation run or multi-sample generation directory: {path}")
    root = absolute(cfg.paths.runs)
    patterns = [f"*-{cfg.label}", f"hydra/*/generation/*-{cfg.label}",
                f"hydra/multirun/*/*/generation/*-{cfg.label}",
                f"hydra/*/generation/sample_*/*-{cfg.label}",
                f"hydra/multirun/*/*/generation/sample_*/*-{cfg.label}"]
    matches = [p for pattern in patterns for p in root.glob(pattern) if completed(p)]
    if not matches:
        raise ValueError(f"No completed generation run for {cfg.label!r}; set evaluation.run_dir")
    newest = max(matches, key=lambda path: (path.stat().st_mtime_ns, path.name))
    if SAMPLE_DIR.fullmatch(newest.parent.name):
        return sample_runs(newest.parent.parent, partial)
    return [newest]


def find_run(cfg):
    runs = find_runs(cfg)
    if len(runs) != 1:
        raise ValueError(f"Expected one generation run, found {len(runs)} samples")
    return runs[0]


def ensure_vendored_binary(path):
    """Extract a vendored `<binary>.xz` next to itself, verifying SHA256SUMS.

    An already-extracted binary is re-verified on every run, so a replaced or
    corrupted executable cannot stand in for the pinned release.
    """
    archive = path.with_name(path.name + ".xz")
    if not archive.is_file():
        return
    sums = {}
    for line in (path.parent / "SHA256SUMS").read_text().splitlines():
        digest, _, name = line.strip().partition("  ")
        sums[name] = digest
    def digest(data):
        return hashlib.sha256(data).hexdigest()
    if path.exists():
        if digest(path.read_bytes()) != sums.get(path.name):
            raise ValueError(f"Checksum mismatch for {path}; delete it to re-extract {archive.name}")
        return
    packed = archive.read_bytes()
    if digest(packed) != sums.get(archive.name):
        raise ValueError(f"Checksum mismatch for {archive}")
    data = lzma.decompress(packed)
    if digest(data) != sums.get(path.name):
        raise ValueError(f"Checksum mismatch after extracting {archive}")
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(data)
    partial.chmod(0o755)
    partial.replace(path)
    print(f"Extracted vendored harness binary: {path}", flush=True)


def generation_job(cfg, output, harness_home=None):
    binary = str(cfg.harness.binary)
    if "/" in binary:
        binary = str(absolute(binary))
        if not cfg.dry_run:
            ensure_vendored_binary(Path(binary))
    else:
        resolved = shutil.which(binary)
        # The OpenCode installer updates .bashrc, which zsh and noninteractive
        # shells need not read. Fall back only for its default executable name.
        if not resolved and cfg.harness.name == "opencode" and binary == "opencode":
            installed = Path.home() / ".opencode/bin/opencode"
            if installed.is_file() and os.access(installed, os.X_OK):
                resolved = str(installed)
        binary = resolved or (binary if cfg.dry_run else "")
    if not cfg.dry_run and (not binary or not os.access(binary, os.X_OK)):
        raise ValueError(f"Harness executable not found; set harness.binary (currently {cfg.harness.binary!r})")
    if cfg.harness.config:
        config = absolute(cfg.harness.config)
        if not cfg.dry_run and not config.is_file():
            raise ValueError(f"Missing harness.config: {config}")
    else:
        if cfg.harness.name == "traecli":
            raise ValueError("traecli requires harness.config pointing to an existing profile")
        home = harness_home or output / "harnesses"
        url = endpoint(cfg)
        if not cfg.dry_run and harness_home is None:
            configure(home, url, cfg.model.served_name, cfg.model.context_length,
                      output_tokens=cfg.model.output_tokens, temperature=cfg.model.get("temperature"))
        config = home / ("codex/local.config.toml" if cfg.harness.name == "codex"
                         else "opencode/config/opencode/opencode.json")
        if harness_home is not None and not config.is_file():
            raise ValueError(f"Resumed invocation has no rendered harness config: {config}")
    prefix = {"codex": "CODEX", "opencode": "OPENCODE", "traecli": "TRAE"}[cfg.harness.name]
    # The adapter re-checks the binary against the same pin (empty skips it).
    version = cfg.harness.get("version")
    env = {f"{prefix}_BIN": binary, f"{prefix}_CONFIG": str(config),
           f"{prefix}_VERSION": "" if version is None else str(version),
           "DATASET": str(absolute(cfg.paths.dataset)), "OUTPUT_BASE": str(output / "generation"),
           "LIMIT": str(cfg.generation.limit), "CONCURRENCY": str(cfg.generation.concurrency),
           "AGENT_TIMEOUT": str(cfg.generation.timeout),
           "SAVE_TRAJECTORIES": str(cfg.generation.save_trajectories).lower()}
    return ["bash", str(ROOT / "patcheval/exp_agent/run_infer.sh"), cfg.harness.name, cfg.label], env


def record_harness_version(cfg, binary, output):
    """Record the harness CLI version beside the resolved config.

    Standalone Codex installs update themselves, so the executable behind a bare
    `codex` can change between runs.
    """
    try:
        text = subprocess.check_output([binary, "--version"], text=True, timeout=60,
                                       stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.SubprocessError):
        text = ""
    version = (re.search(r"\d+\.\d+\.\d+", text) or [None])[0]
    record = {"harness": cfg.harness.name, "binary": str(Path(binary).resolve()), "version": version,
              "version_output": text}
    pinned = cfg.harness.get("version")
    if pinned is not None:
        record["pinned_version"] = str(pinned)
        if version != str(pinned):
            raise ValueError(f"{cfg.harness.name} is pinned to {pinned}, but {binary} reports "
                             f"{version or 'an unknown version'}; set harness.binary to the pinned "
                             "release or override harness.version")
    if cfg.harness.name == "opencode" and not cfg.harness.config:
        record["opencode_version_validated"] = OPENCODE_VERSION
        if version != OPENCODE_VERSION:
            print(f"WARNING: rendered OpenCode settings were validated with {OPENCODE_VERSION}, "
                  f"but {binary} reports {version or 'an unknown version'}", file=sys.stderr)
    (output / "harness-version.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def ready_pattern(harness):
    """The adapter's ready marker: printed once the agent app has started."""
    adapter = ROOT / f"patcheval/exp_agent/agents/{harness}.sh"
    match = re.search(r"^AGENT_READY_PATTERN='([^']*)'", adapter.read_text(), re.M) if adapter.is_file() else None
    return match.group(1) if match else ""


def startup_failures(run, pattern):
    """CVEs whose agent never started (no model output), so rerunning them is unbiased.

    Runner records carry startup_failed; older runs are recognised by an agent
    failure whose archived stdout lacks the adapter's ready marker. Genuine
    timeouts and repair failures print the marker and are never selected.
    """
    marker = re.compile(pattern) if pattern else None
    failed = []
    if not (run / "results.jsonl").is_file():
        return failed
    for line in (run / "results.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("patch_generated"):
            continue
        if row.get("startup_failed"):
            failed.append(row["cve"])
            continue
        if "startup_failed" in row or marker is None or "agent failed" not in (row.get("error") or ""):
            continue
        trajectory = Path(row["trajectory_path"]) if row.get("trajectory_path") else None
        if trajectory is None or not trajectory.is_dir():
            matches = list((run / "trajectories").glob(f"*-patcheval_{row['cve']}"))
            trajectory = matches[0] if len(matches) == 1 else None
        stdout = trajectory / "stdout.jsonl" if trajectory else None
        if stdout is None or not stdout.is_file():
            continue
        if not marker.search(stdout.read_text(errors="replace")):
            failed.append(row["cve"])
    return failed


def still_running(base, window_s=900):
    """An unfinished run in BASE that was written recently, i.e. probably live."""
    if not base.is_dir():
        return None
    now = time.time()
    for run in base.iterdir():
        if not run.is_dir() or completed(run):
            continue
        work = run / ".work"
        paths = [run, run / "results.jsonl", work] + (list(work.iterdir()) if work.is_dir() else [])
        if any(path.exists() and now - path.stat().st_mtime < window_s for path in paths):
            return run
    return None


def prepare_resume(cfg, output):
    """Adopt a previous generation invocation's settings and list its missing samples."""
    target = absolute(cfg.generation.resume_dir)
    stored_file = target / "resolved.yaml"
    if not stored_file.is_file():
        raise ValueError(f"Not a generation invocation (no resolved.yaml): {target}")
    stored = OmegaConf.load(stored_file)
    if stored.get("action") != "generate":
        raise ValueError(f"{target} is not a generation invocation")
    # Every setting that shapes a sample comes from the original invocation, so
    # resumed samples are drawn under identical conditions.
    for key in ("model", "harness", "label"):
        OmegaConf.update(cfg, key, stored[key], merge=False)
    for key in ("limit", "concurrency", "timeout", "samples", "save_trajectories"):
        OmegaConf.update(cfg, f"generation.{key}", stored.generation[key])
    for key in ("host", "port", "base_url"):
        OmegaConf.update(cfg, f"server.{key}", stored.server[key])
    cfg.paths.dataset = stored.paths.dataset
    validate(cfg)
    harness_home = None if cfg.harness.config else target / "harnesses"
    samples = cfg.generation.samples
    bases = [target / "generation" / (f"sample_{i}" if samples > 1 else "") for i in range(samples)]
    pending, reruns = [], []
    pattern = ready_pattern(cfg.harness.name)
    for i, base in enumerate(bases):
        runs = [run for run in base.iterdir() if completed(run)] if base.is_dir() else []
        if not runs:
            active = still_running(base)
            if active:
                raise ValueError(f"Sample {i} appears to be still generating ({active} was updated in the "
                                 "last 15 minutes); resume after that run finishes or is stopped")
            pending.append(i)
        elif len(runs) == 1 and (cves := startup_failures(runs[0], pattern)):
            reruns.append((i, runs[0], cves))
    print(f"Resuming {target}: samples {pending or 'none'} of {samples} still to generate", flush=True)
    for i, run, cves in reruns:
        print(f"  sample {i}: rerunning {len(cves)} tasks whose agent never started", flush=True)
    return target, harness_home, pending, reruns


def check_resume_version(target, record):
    stored = target / "harness-version.json"
    if stored.is_file():
        previous = json.loads(stored.read_text()).get("version")
        if previous and record["version"] != previous:
            raise ValueError(f"Harness version changed since {target} was generated "
                             f"({previous} -> {record['version']}); set harness.binary to the "
                             "original release to keep samples comparable")


def evaluation_jobs(cfg, output):
    """Conversion and evaluation commands for each sample; one sample keeps the flat layout."""
    runs = find_runs(cfg)
    evaluator = ROOT / "patcheval/evaluation"
    jobs, results = [], []
    for run in runs:
        suffix = run.parent.name if SAMPLE_DIR.fullmatch(run.parent.name) else ""
        patch_input = output / "eval_inputs" / suffix / "patches.jsonl"
        report = output / "evaluation_output" / suffix
        # The evaluator prefixes its output argument with ./evaluation_output/.
        relative_output = os.path.relpath(report, evaluator / "evaluation_output")
        jobs.append(([sys.executable, str(ROOT / "patcheval/exp_agent/process_data.py"),
                      "--runner-output", str(run), "--process-data-path", str(patch_input),
                      "--test-data-path", str(absolute(cfg.paths.dataset))], ROOT))
        jobs.append(([sys.executable, str(evaluator / "run_evaluation.py"),
                      "--output", relative_output, "--patch_file", str(patch_input),
                      "--input_file", str(absolute(cfg.paths.dataset)),
                      "--max_workers", str(cfg.evaluation.max_workers),
                      "--log_level", cfg.evaluation.log_level], evaluator))
        results.append((patch_input, report / "summary.json"))
    return runs, jobs, results


def save_config(cfg, output):
    # Hydra also preserves its original config and overrides under .hydra/.
    output.mkdir(parents=True, exist_ok=True)
    OmegaConf.save(cfg, output / "resolved.yaml", resolve=True)


def run_command(command, overrides, cfg, cwd=ROOT):
    if cfg.dry_run:
        print(json.dumps({"argv": command, "environment": overrides, "cwd": str(cwd)}, indent=2))
        return
    env = {**os.environ, **overrides}
    # The existing Bash runners invoke `python`; use this project's interpreter.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def dispatch(cfg, output):
    validate(cfg)
    output = absolute(output)
    if cfg.action == "setup":
        python = absolute(cfg.paths.serving_env) / "bin/python"
        commands = []
        if not python.is_file():
            commands.append(["uv", "venv", "--managed-python", "--python", str(cfg.server.python_version), str(python.parent.parent)])
        commands.append(["uv", "pip", "install", "--python", str(python), f"vllm=={cfg.server.version}"])
        save_config(cfg, output)
        for command in commands:
            run_command(command, serving_environment(cfg), cfg)
    elif cfg.action == "serve":
        command = serve_command(cfg)
        save_config(cfg, output)
        env = serving_environment(cfg)
        if cfg.dry_run:
            run_command(command, env, cfg)
            return
        python = absolute(cfg.paths.serving_env) / "bin/python"
        if not python.is_file() or not os.access(command[0], os.X_OK):
            raise ValueError("Run action=setup first, or set paths.serving_env")
        if not shutil.which("cc") or not shutil.which("nvidia-smi"):
            raise ValueError("Serving requires a C compiler and nvidia-smi")
        subprocess.run(["nvidia-smi", f"--id={cfg.server.gpu}", "--query-gpu=name,memory.total,memory.free", "--format=csv"], check=True)
        subprocess.run([str(python), "-c", 'import vllm, pathlib, sysconfig; assert (pathlib.Path(sysconfig.get_path("include")) / "Python.h").exists(), "Python development headers are required"'], check=True)
        version = subprocess.check_output([str(python), "-c", 'import importlib.metadata; print(importlib.metadata.version("vllm"))'], text=True).strip()
        (output / "server-version.json").write_text(json.dumps({"vllm": version}) + "\n")
        # Generation invocations copy this record, so each run states the engine
        # settings it was sampled under and resumes can refuse a changed server.
        current = current_server_file(cfg)
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(json.dumps({
            "argv": command[1:], "vllm": version, "serve_invocation": str(output),
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}, indent=2) + "\n")
        logging.shutdown()
        os.execvpe(command[0], command, {**os.environ, **env})
    elif cfg.action == "configure":
        cfg.configure.output_dir = str(absolute(cfg.configure.output_dir) if cfg.configure.output_dir
                                       else output / "harnesses")
        url = endpoint(cfg)
        save_config(cfg, output)
        if cfg.dry_run:
            print(f"Would render harness configs in {absolute(cfg.configure.output_dir)} for {url}")
            return
        home = configure(absolute(cfg.configure.output_dir), url, cfg.model.served_name,
                         cfg.model.context_length, cfg.configure.force, cfg.model.output_tokens,
                         cfg.model.get("temperature"))
        print(f"Generated Codex and OpenCode configs in {home}")
        print("action=generate renders fresh local configs automatically unless harness.config is set.")
    elif cfg.action == "check":
        url = endpoint(cfg)
        save_config(cfg, output)
        if cfg.dry_run:
            print(f"Would check {url}: model={cfg.model.served_name}, protocol={cfg.check.protocol}")
            return
        if run_checks(url, cfg.model.served_name, cfg.check.protocol, cfg.check.timeout, cfg.model.output_tokens):
            raise SystemExit(1)
    elif cfg.action == "analyze":
        from scripts.infer.analyze_trajectories import analyze
        if not cfg.analysis.input_dir:
            raise ValueError("Set analysis.input_dir to a generation run or trajectory directory")
        save_config(cfg, output)
        destination = absolute(cfg.analysis.output_dir) if cfg.analysis.output_dir else output / "analysis"
        if cfg.dry_run:
            print(f"Would normalize trajectories from {absolute(cfg.analysis.input_dir)} into {destination}")
            return
        summary = analyze(absolute(cfg.analysis.input_dir), destination)
        print(f"Normalized {summary['completed_tasks']} tasks: {destination}")
    elif cfg.action == "evaluate":
        runs, jobs, results = evaluation_jobs(cfg, output)
        sampled = bool(SAMPLE_DIR.fullmatch(runs[0].parent.name))
        cfg.evaluation.run_dir = str(runs[0].parent.parent if sampled else runs[0])
        save_config(cfg, output)
        for command, cwd in jobs:
            run_command(command, {}, cfg, cwd=cwd)
        print(f"Evaluation output: {output / 'evaluation_output'}")
        if not cfg.dry_run:
            expected = configured_samples(runs[0].parent.parent) if sampled else 1
            summary = aggregate(results, output / "pass_at_k.json", {
                "sample_runs": [str(run) for run in runs],
                "configured_samples": expected or len(runs),
                "partial": bool(expected) and len(runs) < expected})
            scores = "  ".join(f"pass@{k}={summary[f'pass@{k}']:.2%}" for k in range(1, summary["n_samples"] + 1))
            note = f" (PARTIAL: {len(runs)} of {expected} samples)" if summary["partial"] else ""
            print(f"{summary['n_cves']} CVEs x {summary['n_samples']} samples: {scores}{note}")
            print(f"pass@k summary: {output / 'pass_at_k.json'}")
    else:
        target, harness_home, pending, reruns = output, None, None, []
        if cfg.generation.get("resume_dir"):
            target, harness_home, pending, reruns = prepare_resume(cfg, output)
        command, env = generation_job(cfg, output, harness_home)
        save_config(cfg, output)
        prefix = {"codex": "CODEX", "opencode": "OPENCODE", "traecli": "TRAE"}[cfg.harness.name]
        if not cfg.dry_run:
            record = record_harness_version(cfg, env[f"{prefix}_BIN"], output)
            server = record_server(cfg, output)
            if target != output:
                check_resume_version(target, record)
                check_resume_server(target, server)
        # Independent samples for pass@k run one after another against the same
        # server and harness config, each in its own generation/sample_<i>/.
        samples = cfg.generation.samples
        incomplete, rerun_log = [], []
        # Completed samples first: rerun, in place, tasks whose agent never started.
        for index, run, cves in reruns:
            listing = output / f"rerun_sample_{index}.txt"
            if not cfg.dry_run:
                listing.write_text("\n".join(cves) + "\n")
            rerun_env = {**env, "OUTPUT_BASE": str(run.parent), "RERUN_INTO": str(run),
                         "RERUN_CVES_FILE": str(listing)}
            print(f"Rerunning {len(cves)} startup failures in sample {index}", flush=True)
            before = (run / "startup_reruns.jsonl").read_text() if (run / "startup_reruns.jsonl").is_file() else ""
            try:
                run_command(command, rerun_env, cfg)
            except subprocess.CalledProcessError as exc:
                after = (run / "startup_reruns.jsonl").read_text() if (run / "startup_reruns.jsonl").is_file() else ""
                if after == before:
                    raise ValueError(f"Rerun in sample {index} did not complete (runner exit {exc.returncode})") from exc
            rerun_log.append({"sample": index, "run": str(run), "cves": cves})
        for index in (pending if pending is not None else range(samples)):
            base = target / "generation" / (f"sample_{index}" if samples > 1 else "")
            sample_env = {**env, "OUTPUT_BASE": str(base)}
            if samples > 1:
                print(f"Generation sample {index + 1}/{samples}", flush=True)
            before = {run for run in base.iterdir() if completed(run)} if base.is_dir() else set()
            try:
                run_command(command, sample_env, cfg)
            except subprocess.CalledProcessError as exc:
                # The runner exits 1 when any task fails; a finished run with failed
                # repairs is still a complete sample and is evaluated as such.
                finished = [run for run in base.iterdir() if completed(run)] if base.is_dir() else []
                if not set(finished) - before:
                    incomplete.append(index)
                    print(f"Sample {index} did not complete (runner exit {exc.returncode})",
                          file=sys.stderr, flush=True)
                else:
                    print(f"Sample {index} completed with failed tasks (runner exit {exc.returncode}); "
                          "continuing", flush=True)
        if target != output and not cfg.dry_run:
            with (target / "resumed_by.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"invocation": str(output), "samples": list(pending),
                                      "incomplete": incomplete, "startup_reruns": rerun_log}) + "\n")
        if incomplete:
            raise ValueError(f"Samples {incomplete} did not complete; rerun with "
                             f"generation.resume_dir={target}")


@hydra.main(version_base="1.3", config_path=str(ROOT / "scripts/conf"), config_name="config")
def main(cfg: DictConfig):
    try:
        dispatch(cfg, HydraConfig.get().runtime.output_dir)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def entrypoint(action=None):
    # Hydra resolves explicit relative run/sweep directories before dispatch.
    # Anchor its initialization as well as our own paths at the repository root.
    os.chdir(ROOT)
    if action:
        sys.argv.append(f"action={action}")
    main()


if __name__ == "__main__":
    entrypoint()
