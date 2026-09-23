#!/usr/bin/env python3
"""Generate patches for PatchEval cases with a configurable CLI agent.

This is a patch-generation-only runner. Evaluation is handled separately by
../evaluation/run_evaluation.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import time
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_TIMEOUT_S = 2400
STREAM_READER_LIMIT = 16 * 1024 * 1024


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    # True when the agent never emitted its ready marker (it failed to start).
    startup_stalled: bool = False


@dataclass
class GenerationResult:
    index: int
    cve: str
    instance_id: str
    image: str
    workdir: str
    container_name: str
    status: str
    patch_generated: bool
    agent_exit_code: Optional[int]
    timed_out: bool
    duration_s: float
    patch_path: str
    error: str = ""
    trajectory_path: Optional[str] = None
    startup_attempts: int = 1
    startup_failed: bool = False


def _safe_name(value: str, max_len: int = 100) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return (value.strip(".-") or "sample")[:max_len]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _require_dataset_images(samples: list[dict[str, Any]]) -> None:
    missing: list[str] = []
    for sample in samples:
        cve = str(sample["cve_id"])
        if not sample.get("image_url"):
            missing.append(cve)
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "" if len(missing) <= 10 else f", ... ({len(missing)} total)"
        raise ValueError(f"dataset samples missing image_url: {preview}{suffix}")


def _image_url(sample: dict[str, Any]) -> str:
    image = str(sample.get("image_url") or "").strip()
    if not image:
        raise ValueError(f"dataset sample {sample.get('cve_id', '<unknown>')} missing image_url")
    return image


def _repo_basename(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _prompt(sample: dict[str, Any], workdir_hint: str) -> str:
    return (
        "## USER\n\n"
        "Please fix the vulnerabilities in the code repository based on the following information:"
        + str(sample.get("cve_description") or "").strip()
        + "\n\n"
        + "Task runtime information:\n"
        + "- Target workdir: "
        + workdir_hint
        + "\n"
        + "- All tool path arguments must stay under this directory.\n"
        + "- Start exploration from this workspace root instead of guessing a path under /workspace.\n"
        + "- Before stopping, write the final repository diff to `/workspace/fix.patch` from the target workdir."
        + "\n\n"
        + "Repair-source restrictions:\n"
        + "- Do not search the web for this vulnerability, CVE, advisory, GHSA, release note, issue, pull request, or upstream patch.\n"
        + "- Do not run network commands such as curl, wget, git fetch, git pull, git ls-remote, npm view, pip index, or package/advisory lookups to find the fix.\n"
    )


async def _run(args: list[str], *, timeout_s: Optional[int] = None) -> CommandResult:
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_READER_LIMIT,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        out_b, err_b = await proc.communicate()
        timed_out = True
    return CommandResult(
        command=args,
        exit_code=int(proc.returncode if proc.returncode is not None else 124),
        stdout=(out_b or b"").decode("utf-8", errors="replace"),
        stderr=(err_b or b"").decode("utf-8", errors="replace"),
        duration_s=time.monotonic() - started,
        timed_out=timed_out,
    )


async def _docker_exec(container: str, command: str, *, workdir: str = "/", timeout_s: int = 600) -> CommandResult:
    args = ["docker", "exec", "-w", workdir, container, "bash", "-lc", command]
    return await _run(args, timeout_s=timeout_s)


def _parse_mounts(items: list[str]) -> list[tuple[str, str, str]]:
    mounts = []
    for item in items:
        parts = item.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"expected HOST:CONTAINER[:ro|rw], got: {item}")
        mode = parts[2] if len(parts) == 3 else "rw"
        if mode not in {"ro", "rw"}:
            raise ValueError(f"invalid mount mode: {item}")
        mounts.append((str(Path(parts[0]).expanduser().resolve()), parts[1], mode))
    return mounts


def _docker_run_args(container: str, image: str, result_dir: Path, mounts: list[tuple[str, str, str]]) -> list[str]:
    cmd = ["docker", "run", "-d", "--name", container, "-v", f"{result_dir.resolve()}:/results:rw"]
    for host, dst, mode in mounts:
        cmd.extend(["-v", f"{host}:{dst}:{mode}"])
    cmd.extend([image, "bash", "-lc", "tail -f /dev/null"])
    return cmd


async def _detect_workdir(container: str, sample: dict[str, Any]) -> str:
    workdir_hint = str(sample.get("workdir") or "").rstrip("/")
    if workdir_hint:
        check = await _docker_exec(container, f"test -d {shlex.quote(workdir_hint)}/.git", timeout_s=60)
        if check.exit_code == 0:
            return workdir_hint
        raise RuntimeError(f"dataset workdir is not a git repository in image: {workdir_hint}")

    repo_name = _repo_basename(str(sample.get("repo") or ""))
    if repo_name:
        check = await _docker_exec(container, f"test -d /workspace/{shlex.quote(repo_name)}/.git", timeout_s=60)
        if check.exit_code == 0:
            return f"/workspace/{repo_name}"
        lower_repo_name = repo_name.lower()
        if lower_repo_name != repo_name:
            check = await _docker_exec(container, f"test -d /workspace/{shlex.quote(lower_repo_name)}/.git", timeout_s=60)
            if check.exit_code == 0:
                return f"/workspace/{lower_repo_name}"

    check = await _docker_exec(container, "test -d /workspace/.git", timeout_s=60)
    if check.exit_code == 0:
        return "/workspace"

    if repo_name:
        raise RuntimeError(
            f"could not locate git workdir for repo {repo_name!r}; "
            "check that dataset image_url matches this CVE or add dataset workdir"
        )

    find_repo = await _docker_exec(
        container,
        "find /workspace -mindepth 2 -maxdepth 3 -type d -name .git 2>/dev/null | head -n 2",
        timeout_s=60,
    )
    candidates = [line.rsplit("/.git", 1)[0] for line in find_repo.stdout.splitlines() if line.strip()]
    if len(candidates) == 1:
        return candidates[0]

    return "/workspace"


async def _hide_workspace_payload(container: str, workdir: str, session_key: str) -> None:
    if workdir.rstrip("/") == "/workspace":
        result = await _docker_exec(container, "rm -f /workspace/fix.patch", timeout_s=300)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout)
        return

    if not workdir.startswith("/workspace/"):
        raise RuntimeError(f"target workdir is outside /workspace: {workdir}")

    script = f"""
set -e
rm -f /workspace/fix.patch
workdir={shlex.quote(workdir)}
top_name=${{workdir#/workspace/}}
top_name=${{top_name%%/*}}
find /workspace -mindepth 1 -maxdepth 1 ! -name "$top_name" -exec rm -rf -- {{}} + 2>/dev/null || true
"""
    result = await _docker_exec(container, script, timeout_s=300)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout)


async def _run_agent(container: str, workdir: str, command_template: str, result_dir: Path, env: dict[str, str],
                     timeout_s: int, ready_pattern: Optional[str] = None,
                     startup_timeout: Optional[float] = None) -> CommandResult:
    """Run the agent; with ready_pattern, stop early if it never starts.

    The agent counts as started once its stdout matches ready_pattern. If that
    does not happen within startup_timeout seconds, or the agent exits first,
    the result is marked startup_stalled so the caller can retry from a fresh
    container; the agent timeout then applies only to agents that started.
    """
    values = {
        "prompt_file": "/results/prompt.txt",
        "workdir": workdir,
    }
    command = command_template.format(**values)
    started = time.monotonic()
    watch = re.compile(ready_pattern.encode()) if ready_pattern and startup_timeout else None
    # Direct file descriptors avoid communicate() losing buffered output when
    # wait_for cancels it on timeout. Raw bytes remain available even on failure.
    stdout_path = result_dir / "agent_stdout.txt"
    stderr_path = result_dir / "agent_stderr.txt"

    def ready_seen() -> bool:
        return bool(watch.search(stdout_path.read_bytes())) if stdout_path.exists() else False

    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-w", workdir,
            *sum((["-e", f"{k}={v}"] for k, v in env.items()), []),
            container, "bash", "-lc", command,
            stdout=out, stderr=err,
        )
        timed_out = stalled = False
        try:
            if watch is None:
                await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            else:
                ready = False
                while True:
                    now = time.monotonic()
                    remaining = started + timeout_s - now
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=remaining if ready else min(1.0, remaining))
                        break
                    except asyncio.TimeoutError:
                        if ready:
                            raise
                    ready = ready_seen()
                    if not ready and time.monotonic() - started >= startup_timeout:
                        stalled = True
                        proc.kill()
                        await proc.wait()
                        break
                if not stalled and not ready:
                    stalled = not ready_seen()
        except asyncio.TimeoutError:
            timed_out = True
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
    # Only diagnostic tails enter memory; the full streams stay on disk.
    def tail(path):
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 8192))
            return stream.read().decode("utf-8", errors="replace")
    return CommandResult(["docker", "exec", container, "bash", "-lc", command],
                         int(proc.returncode if proc.returncode is not None else 124),
                         tail(stdout_path), tail(stderr_path), time.monotonic() - started, timed_out,
                         stalled)


def _trajectory_spec(value: str) -> tuple[str, str]:
    name, separator, path = value.partition("=")
    if not separator or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name) or not path.startswith("/"):
        raise argparse.ArgumentTypeError("trajectory path must be NAME=/absolute/container/path")
    return name, path


async def _archive_trajectory(container: str, work: Path, destination: Path,
                              native_paths: list[tuple[str, str]]) -> dict[str, Any]:
    """Freeze writers before copying session DB/WAL files; never archive homes."""
    destination.mkdir(parents=True, exist_ok=True)
    capture: dict[str, Any] = {"native": {}, "warnings": []}
    for source, target in (("prompt.txt", "prompt.txt"), ("agent_stdout.txt", "stdout.jsonl"),
                           ("agent_stderr.txt", "stderr.txt")):
        path = work / source
        if path.exists():
            shutil.copyfile(path, destination / target)
    stopped = await _run(["docker", "stop", "--time", "5", container], timeout_s=30)
    if stopped.exit_code != 0:
        capture["warnings"].append("Container stop failed; native session files may be incomplete: " + stopped.stderr)
    if native_paths:
        (destination / "native").mkdir(exist_ok=True)
    for name, path in native_paths:
        copied = await _run(["docker", "cp", f"{container}:{path}", str(destination / "native" / name)], timeout_s=120)
        capture["native"][name] = {"source": path, "saved": copied.exit_code == 0}
        if copied.exit_code != 0:
            capture["native"][name]["error"] = copied.stderr or copied.stdout
    return capture


async def _collect_patch(container: str, workdir: str, result_dir: Path) -> CommandResult:
    script = f"""
set -e
cd {shlex.quote(workdir)}
if [ -s /workspace/fix.patch ]; then
  cp /workspace/fix.patch /results/llm.patch
elif git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff HEAD -U3 > /results/llm.patch
else
  echo "target workdir is not a git repository: $(pwd)" >&2
  exit 1
fi
test -s /results/llm.patch
"""
    return await _docker_exec(container, script, workdir=workdir, timeout_s=300)


async def _remove_container(container: str) -> None:
    await _run(["docker", "rm", "-f", container], timeout_s=120)


async def _run_one(sample: dict[str, Any], index: int, args: argparse.Namespace, sem: asyncio.Semaphore, mounts: list[tuple[str, str, str]]) -> GenerationResult:
    async with sem:
        started = time.monotonic()
        cve = str(sample["cve_id"])
        instance_id = f"patcheval_{cve}"
        image = _image_url(sample)
        run_id = f"{index:05d}-{instance_id}"
        container = f"{args.container_prefix}-{_safe_name(run_id)}-{os.getpid()}"
        run_root = Path(args.run_root)
        work = run_root / ".work" / run_id
        work.mkdir(parents=True, exist_ok=True)
        patch_path = run_root / "patches" / f"{cve}.patch"
        trajectory = run_root / "trajectories" / run_id if args.save_trajectories else None
        capture = {}
        if trajectory:
            trajectory.mkdir(parents=True, exist_ok=True)
            _write_json(trajectory / "metadata.json", {"schema_version": 1, "cve": cve,
                        "index": index, "status": "running", "work_logs": str(work)})
        status = "failed"
        error = ""
        workdir = "/workspace"
        agent_result: Optional[CommandResult] = None
        timed_out = False
        ready_pattern = getattr(args, "ready_pattern", "")
        startup_timeout = float(getattr(args, "startup_timeout", 0) or 0)
        watchdog = bool(ready_pattern) and startup_timeout > 0
        attempts = 1 + (int(getattr(args, "startup_retries", 0)) if watchdog else 0)
        stalls: list[dict[str, Any]] = []
        startup_failed = False
        try:
            env = {"PATCHAGENT_SESSION_ID": container}
            for attempt in range(1, attempts + 1):
                run_result = await _run(_docker_run_args(container, image, work, mounts), timeout_s=1200)
                if run_result.exit_code != 0:
                    raise RuntimeError(f"docker run failed: {run_result.stderr or run_result.stdout}")
                workdir = await _detect_workdir(container, sample)
                await _hide_workspace_payload(container, workdir, container)
                prompt = _prompt(sample, workdir)
                (work / "prompt.txt").write_text(prompt, encoding="utf-8")
                watch = ({"ready_pattern": ready_pattern, "startup_timeout": startup_timeout}
                         if watchdog else {})
                agent_result = await _run_agent(container, workdir, args.agent_command, work, env,
                                                args.agent_timeout, **watch)
                if not agent_result.startup_stalled:
                    break
                # The agent never started, so no model output exists: retrying from a
                # fresh container cannot bias the result. Keep the stalled logs.
                stalls.append({"attempt": attempt, "waited_s": round(agent_result.duration_s, 1),
                               "exit_code": agent_result.exit_code})
                if attempt == attempts:
                    startup_failed = True
                    raise RuntimeError(f"agent did not start after {attempts} attempts")
                kept = work / f"startup_attempt_{attempt}"
                kept.mkdir(exist_ok=True)
                for name in ("agent_stdout.txt", "agent_stderr.txt"):
                    if (work / name).exists():
                        shutil.move(str(work / name), str(kept / name))
                _log(f"{run_id}: agent did not start within {startup_timeout:.0f}s "
                     f"(attempt {attempt}/{attempts}); retrying in a fresh container")
                await _remove_container(container)
            timed_out = agent_result.timed_out
            if agent_result.exit_code != 0:
                raise RuntimeError(f"agent failed with exit_code={agent_result.exit_code}")
            collect = await _collect_patch(container, workdir, work)
            if collect.exit_code != 0:
                raise RuntimeError(f"collect patch failed: {collect.stderr or collect.stdout}")
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / "llm.patch", patch_path)
            status = "generated"
        except Exception as exc:
            error = str(exc)
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text("", encoding="utf-8")
            _log(f"{run_id}: failed: {error}")
        finally:
            try:
                if trajectory:
                    capture = await _archive_trajectory(container, work, trajectory, args.trajectory_path)
            except Exception as exc:
                capture = {"warnings": [f"Trajectory archive failed: {exc}"], "work_logs": str(work)}
                _log(f"{run_id}: trajectory archive failed: {exc}")
            finally:
                await _remove_container(container)
        result = GenerationResult(index, cve, instance_id, image, workdir, container, status,
                                  status == "generated", agent_result.exit_code if agent_result else None,
                                  timed_out, time.monotonic() - started, str(patch_path), error,
                                  str(trajectory) if trajectory else None,
                                  startup_attempts=len(stalls) + (0 if startup_failed else 1),
                                  startup_failed=startup_failed)
        if trajectory:
            _write_json(trajectory / "metadata.json", {"schema_version": 1, **asdict(result),
                        "capture": capture, "work_logs": str(work), "startup_stalls": stalls,
                        "agent_duration_s": agent_result.duration_s if agent_result else None})
        return result


def _select(samples: list[dict[str, Any]], args: argparse.Namespace) -> list[tuple[int, dict[str, Any]]]:
    """Choose cases, keeping each case's dataset index (it names its run_id)."""
    indexed = list(enumerate(samples))
    if not args.only_cves_file:
        return indexed if args.limit < 0 else indexed[:args.limit]
    wanted = {line.strip() for line in Path(args.only_cves_file).read_text(encoding="utf-8").splitlines()
              if line.strip()}
    selected = [(idx, sample) for idx, sample in indexed if str(sample["cve_id"]) in wanted]
    missing = wanted - {str(sample["cve_id"]) for _, sample in selected}
    if missing:
        raise ValueError(f"--only-cves-file names CVEs absent from the dataset: {sorted(missing)[:5]}")
    return selected


def _prepare_rerun(run_root: Path, selected: list[tuple[int, dict[str, Any]]]) -> Path:
    """Move the replaced cases' artifacts aside before rerunning them in place."""
    if not (run_root / "results.jsonl").is_file():
        raise ValueError(f"--rerun-into needs a completed run directory: {run_root}")
    rerun_dir = run_root / "startup_reruns" / time.strftime("%Y%m%d_%H%M%S")
    replaced = rerun_dir / "replaced"
    for idx, sample in selected:
        run_id = f"{idx:05d}-patcheval_{sample['cve_id']}"
        for kind in ("trajectories", ".work"):
            source = run_root / kind / run_id
            if source.exists():
                (replaced / kind).mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(replaced / kind / run_id))
    rerun_dir.mkdir(parents=True, exist_ok=True)
    return rerun_dir


def _merge_rerun(run_root: Path, rerun_dir: Path, results: list[GenerationResult]) -> list[dict[str, Any]]:
    """Replace rerun cases' rows in place and recompute the run summary."""
    new = {r.cve: asdict(r) for r in results}
    rows = [json.loads(line) for line in (run_root / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    merged = [new.pop(row["cve"], row) for row in rows] + list(new.values())
    partial = run_root / "results.jsonl.partial"
    partial.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8")
    partial.replace(run_root / "results.jsonl")
    generated = sum(bool(row.get("patch_generated")) for row in merged)
    _write_json(run_root / "summary.json", {"total": len(merged), "generated": generated,
                                            "failed": len(merged) - generated})
    record = {"rerun_dir": str(rerun_dir), "cves": [r.cve for r in results],
              "generated": sum(r.patch_generated for r in results),
              "startup_failed": sum(r.startup_failed for r in results)}
    with (run_root / "startup_reruns.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return merged


async def _main(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser()
    samples = _read_json(input_path if input_path.is_absolute() else ROOT / input_path)
    selected = _select(samples, args)
    _require_dataset_images([sample for _, sample in selected])
    rerun_dir = None
    if args.rerun_into:
        run_root = Path(args.rerun_into).expanduser().resolve()
        rerun_dir = _prepare_rerun(run_root, selected)
        _write_json(rerun_dir / "run_metadata.json", vars(args) | {"run_root": str(run_root),
                    "total_cases": len(selected)})
    else:
        output_base = Path(args.output_dir).expanduser()
        output_base = (output_base if output_base.is_absolute() else ROOT / output_base).resolve()
        output_base.mkdir(parents=True, exist_ok=True)
        run_root = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d_%H%M%S-"),
                                        suffix=f"-{_safe_name(args.run_label or 'run')}",
                                        dir=output_base))
    for sub in ["patches", ".work"]:
        (run_root / sub).mkdir(parents=True, exist_ok=True)
    args.run_root = str(run_root)
    if rerun_dir is None:
        _write_json(run_root / "run_metadata.json", vars(args) | {"run_root": str(run_root), "total_cases": len(selected)})
    mounts = _parse_mounts(args.mount)
    sem = asyncio.Semaphore(args.concurrency)
    tasks = [asyncio.create_task(_run_one(sample, idx, args, sem, mounts)) for idx, sample in selected]
    results_path = (rerun_dir / "results.jsonl") if rerun_dir else run_root / "results.jsonl"
    results = []
    with results_path.open("w", encoding="utf-8") as f:
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            result = await task
            results.append(result)
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
            f.flush()
            _log(f"progress {i}/{len(tasks)}: {result.cve} {result.status}")
    generated = sum(r.patch_generated for r in results)
    if rerun_dir:
        merged = _merge_rerun(run_root, rerun_dir, results)
        print(f"Reran {len(results)} cases in {run_root}: {generated} generated; "
              f"run now {sum(bool(r.get('patch_generated')) for r in merged)}/{len(merged)}")
    else:
        _write_json(run_root / "summary.json", {"total": len(results), "generated": generated, "failed": len(results) - generated})
        print(f"Run directory: {run_root}")
        print(f"Generated patches: {generated}/{len(results)}")
    return 0 if generated == len(results) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default=str(ROOT / "patcheval/datasets/patcheval_verified.json"))
    p.add_argument("--output-dir", default=str(ROOT / "patcheval/exp_agent/agent_runs"))
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--run-label", default="")
    p.add_argument("--agent-command", required=True)
    p.add_argument("--mount", action="append", default=[])
    p.add_argument("--agent-timeout", type=int, default=DEFAULT_AGENT_TIMEOUT_S)
    p.add_argument("--container-prefix", default="patcheval-agent")
    p.add_argument("--save-trajectories", action="store_true",
                   help="Archive full task prompts, CLI streams, and configured native sessions")
    p.add_argument("--trajectory-path", type=_trajectory_spec, action="append", default=[],
                   help="Optional native session artifact NAME=/container/path (never a credential home)")
    p.add_argument("--ready-pattern", default="",
                   help="Regex the agent prints to stdout once it has started; enables the startup watchdog")
    p.add_argument("--startup-timeout", type=float, default=300,
                   help="Seconds to wait for --ready-pattern before retrying in a fresh container")
    p.add_argument("--startup-retries", type=int, default=2,
                   help="Fresh-container retries for an agent that never starts")
    p.add_argument("--only-cves-file", default="",
                   help="File of CVE IDs to run (one per line); keeps their dataset indices and ignores --limit")
    p.add_argument("--rerun-into", default="",
                   help="Existing run directory whose selected cases are rerun and replaced in place")
    return p


def main() -> int:
    return asyncio.run(_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
