#!/usr/bin/env python3
"""Hydra entry point for local serving and PatchEval generation/evaluation."""

import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.infer.check_server import run_checks
from scripts.infer.configure_harnesses import configure


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
                "generation.timeout", "evaluation.max_workers"):
        positive(OmegaConf.select(cfg, key), key)
    if cfg.model.output_tokens >= cfg.model.context_length:
        raise ValueError("model.output_tokens must be smaller than model.context_length")
    if cfg.server.port > 65535 or not 0 < cfg.server.memory_fraction < 1:
        raise ValueError("Invalid server port or memory_fraction (must be between 0 and 1)")
    if not isinstance(cfg.generation.limit, int) or cfg.generation.limit == 0 or cfg.generation.limit < -1:
        raise ValueError("generation.limit must be -1 or a positive integer")
    if cfg.check.protocol not in {"both", "chat", "responses"} or cfg.check.timeout <= 0:
        raise ValueError("Invalid check.protocol or check.timeout")
    if not OmegaConf.is_list(cfg.server.extra_args) or not all(isinstance(arg, str) for arg in cfg.server.extra_args):
        raise ValueError("server.extra_args must be a list of strings")


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
    return args + list(cfg.server.extra_args)


def find_run(cfg):
    if cfg.evaluation.run_dir:
        path = absolute(cfg.evaluation.run_dir)
        if not path.is_dir() or not (path / "patches").is_dir():
            raise ValueError(f"Not a generation run directory: {path}")
        return path
    root = absolute(cfg.paths.runs)
    patterns = [f"*-{cfg.label}", f"hydra/*/generation/*-{cfg.label}",
                f"hydra/multirun/*/*/generation/*-{cfg.label}"]
    matches = [p for pattern in patterns for p in root.glob(pattern)
               if p.is_dir() and (p / "summary.json").is_file() and (p / "patches").is_dir()]
    if not matches:
        raise ValueError(f"No completed generation run for {cfg.label!r}; set evaluation.run_dir")
    return max(matches, key=lambda path: (path.stat().st_mtime_ns, path.name))


def generation_job(cfg, output):
    binary = str(cfg.harness.binary)
    if "/" in binary:
        binary = str(absolute(binary))
    elif not cfg.dry_run:
        binary = shutil.which(binary) or ""
    if not cfg.dry_run and (not binary or not os.access(binary, os.X_OK)):
        raise ValueError(f"Harness executable not found; set harness.binary (currently {cfg.harness.binary!r})")
    if cfg.harness.config:
        config = absolute(cfg.harness.config)
        if not cfg.dry_run and not config.is_file():
            raise ValueError(f"Missing harness.config: {config}")
    else:
        if cfg.harness.name == "traecli":
            raise ValueError("traecli requires harness.config pointing to an existing profile")
        home = output / "harnesses"
        url = endpoint(cfg)
        if not cfg.dry_run:
            configure(home, url, cfg.model.served_name, cfg.model.context_length,
                      output_tokens=cfg.model.output_tokens)
        config = home / ("codex/local.config.toml" if cfg.harness.name == "codex"
                         else "opencode/config/opencode/opencode.json")
    prefix = {"codex": "CODEX", "opencode": "OPENCODE", "traecli": "TRAE"}[cfg.harness.name]
    env = {f"{prefix}_BIN": binary, f"{prefix}_CONFIG": str(config),
           "DATASET": str(absolute(cfg.paths.dataset)), "OUTPUT_BASE": str(output / "generation"),
           "LIMIT": str(cfg.generation.limit), "CONCURRENCY": str(cfg.generation.concurrency),
           "AGENT_TIMEOUT": str(cfg.generation.timeout),
           "SAVE_TRAJECTORIES": str(cfg.generation.save_trajectories).lower()}
    return ["bash", str(ROOT / "patcheval/exp_agent/run_infer.sh"), cfg.harness.name, cfg.label], env


def evaluation_jobs(cfg, output):
    run = find_run(cfg)
    evaluator = ROOT / "patcheval/evaluation"
    patch_input = output / "eval_inputs/patches.jsonl"
    # The evaluator prefixes its output argument with ./evaluation_output/.
    relative_output = os.path.relpath(output / "evaluation_output", evaluator / "evaluation_output")
    convert = [sys.executable, str(ROOT / "patcheval/exp_agent/process_data.py"),
               "--runner-output", str(run), "--process-data-path", str(patch_input),
               "--test-data-path", str(absolute(cfg.paths.dataset))]
    evaluate = [sys.executable, str(evaluator / "run_evaluation.py"),
                "--output", relative_output, "--patch_file", str(patch_input),
                "--input_file", str(absolute(cfg.paths.dataset)),
                "--max_workers", str(cfg.evaluation.max_workers), "--log_level", cfg.evaluation.log_level]
    return run, [(convert, ROOT), (evaluate, evaluator)]


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
                         cfg.model.context_length, cfg.configure.force, cfg.model.output_tokens)
        print(f"Generated Codex and OpenCode configs in {home}")
        print("action=generate renders fresh local configs automatically unless harness.config is set.")
    elif cfg.action == "check":
        url = endpoint(cfg)
        save_config(cfg, output)
        if cfg.dry_run:
            print(f"Would check {url}: model={cfg.model.served_name}, protocol={cfg.check.protocol}")
            return
        if run_checks(url, cfg.model.served_name, cfg.check.protocol, cfg.check.timeout):
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
        run, jobs = evaluation_jobs(cfg, output)
        cfg.evaluation.run_dir = str(run)
        save_config(cfg, output)
        for command, cwd in jobs:
            run_command(command, {}, cfg, cwd=cwd)
        print(f"Evaluation output: {output / 'evaluation_output'}")
    else:
        command, env = generation_job(cfg, output)
        save_config(cfg, output)
        run_command(command, env, cfg)


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
