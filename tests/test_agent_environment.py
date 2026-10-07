"""Agent-environment features: project Python env, vendored ripgrep, continue-on-length."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from omegaconf import OmegaConf

from patcheval.exp_agent import patch_agent_runner as runner
from scripts import run as workflow
from scripts.infer import token_usage
from tests.test_hydra_workflow import ROOT, config

WRAPPER = ROOT / "patcheval/exp_agent/container/opencode_continue.sh"
START = '{"type":"step_start","timestamp":1,"sessionID":"ses_A","part":{"type":"step-start"}}'
LENGTH = ('{"type":"step_finish","timestamp":2,"sessionID":"ses_A","part":{"reason":"length",'
          '"type":"step-finish","tokens":{"input":10,"output":0,"reasoning":16000,"cache":{"read":0,"write":0}}}}')
STOP = ('{"type":"step_finish","timestamp":3,"sessionID":"ses_A","part":{"reason":"stop",'
        '"type":"step-finish","tokens":{"input":20,"output":5,"reasoning":7,"cache":{"read":0,"write":0}}}}')
ESCAPED = ('{"type":"tool_use","timestamp":4,"sessionID":"ses_A","part":{"tool":"bash","state":'
           '{"output":"{\\"type\\":\\"step_finish\\",\\"reason\\":\\"length\\"}"}}}')
LEGACY_COMMAND = (
    "rm -rf /tmp/opencode-config /tmp/opencode-data && mkdir -p /tmp/opencode-config /tmp/opencode-data && "
    "cp -a /opt/opencode-config-src/. /tmp/opencode-config/ && cp -a /opt/opencode-data-src/. /tmp/opencode-data/ && "
    "unset OPENCODE_ENABLE_EXA OPENCODE_EXPERIMENTAL_EXA && OPENCODE_DISABLE_MODELS_FETCH=1 "
    "OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_DEFAULT_PLUGINS=1 OPENCODE_DISABLE_LSP_DOWNLOAD=1 "
    "OPENCODE_DISABLE_SHARE=1 XDG_CONFIG_HOME=/tmp/opencode-config XDG_DATA_HOME=/tmp/opencode-data "
    "opencode run --format json --auto < {prompt_file}")


class PythonEnvScriptTests(unittest.TestCase):
    """PYTHON_ENV_SCRIPT, run on the host against real virtualenvs."""

    def venv(self, workspace, name):
        path = workspace / "PoC_env" / name
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True)
        script = path / "bin/pytest"
        script.write_text(f"#!{path}/bin/python\nimport sys\nprint(sys.prefix)\n")
        script.chmod(0o755)
        return path

    def run_script(self, tmp, cve):
        env = {**os.environ, "PE_WORKSPACE": str(tmp / "workspace"), "PE_DEST": str(tmp / "opt/project-venv"),
               "PE_ENV_FILE": str(tmp / "etc/profile.d/zz-project-venv.sh"), "CVE": cve}
        out = subprocess.run(["bash", "-c", runner.PYTHON_ENV_SCRIPT], env=env, capture_output=True, text=True,
                             check=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def test_venv_is_relocated_without_the_cve_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = self.venv(tmp / "workspace", "CVE-2099-0001")
            (source / "poc.py").write_text("payload")
            fields = self.run_script(tmp, "CVE-2099-0001")
            dest = tmp / "opt/project-venv"
            self.assertEqual((fields["status"], fields["leaks"], fields["skipped"]), ("relocated", "0", "poc.py,"))
            self.assertTrue((dest / "bin/python").is_symlink())
            self.assertFalse((source / "pyvenv.cfg").exists())
            self.assertTrue((source / "poc.py").exists())  # not moved: the payload step deletes it
            self.assertEqual(subprocess.run([str(dest / "bin/pytest")], capture_output=True, text=True,
                                            check=True).stdout.strip(), str(dest))
            for path in (dest / "bin").iterdir():
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn("CVE-2099-0001", path.read_text(errors="replace"), path)
            self.assertNotIn("CVE-2099-0001", (dest / "pyvenv.cfg").read_text())
            env_file = (tmp / "etc/profile.d/zz-project-venv.sh").read_text()
            self.assertIn(f"export VIRTUAL_ENV={dest}", env_file)
            self.assertIn(f'export PATH="{dest}/bin:$PATH"', env_file)

    def test_none_ambiguous_and_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "workspace").mkdir()
            self.assertEqual(self.run_script(tmp, "CVE-1")["status"], "none")
            self.venv(tmp / "workspace", "CVE-A")
            self.venv(tmp / "workspace", "CVE-B")
            self.assertEqual(self.run_script(tmp, "CVE-X")["status"], "ambiguous")
            self.assertEqual(self.run_script(tmp, "CVE-B")["status"], "relocated")  # the matching one wins
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = self.venv(tmp / "workspace", "CVE-1")
            cfg = (source / "pyvenv.cfg").read_text().splitlines()
            (source / "pyvenv.cfg").write_text("\n".join(
                f"home = {tmp}/workspace/base" if line.startswith("home") else line for line in cfg) + "\n")
            fields = self.run_script(tmp, "CVE-1")
            self.assertEqual((fields["status"], fields["reason"]), ("unsupported", "home-in-workspace"))
            self.assertFalse((tmp / "opt/project-venv").exists())


class ContinueWrapperTests(unittest.TestCase):
    """container/opencode_continue.sh with a scripted fake `opencode` on PATH."""

    def run_wrapper(self, outputs, max_continuations=2, exits=None, sleeps=None, prompt=b"fix the bug",
                    extra_env=None):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fake_dir, bin_dir, record, repo = tmp / "fake", tmp / "bin", tmp / "record", tmp / "repo"
        for path in (fake_dir, bin_dir, record, repo):
            path.mkdir()
        for i, lines in enumerate(outputs, 1):
            (fake_dir / f"out_{i}").write_text("".join(line + "\n" for line in lines))
        for i, code in (exits or {}).items():
            (fake_dir / f"exit_{i}").write_text(str(code))
        for i, seconds in (sleeps or {}).items():
            (fake_dir / f"sleep_{i}").write_text(str(seconds))
        fake = bin_dir / "opencode"
        fake.write_text(f"""#!/bin/bash
dir={fake_dir}
n=$(( $(cat "$dir/count" 2>/dev/null || echo 0) + 1 )); echo $n > "$dir/count"
printf '%s\\n' "$@" > "$dir/args_$n"
cat > "$dir/stdin_$n"
[ -f "$dir/sleep_$n" ] && sleep "$(cat "$dir/sleep_$n")"
[ -f "$dir/out_$n" ] && cat "$dir/out_$n"
exit "$(cat "$dir/exit_$n" 2>/dev/null || echo 0)"
""")
        fake.chmod(0o755)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "a.txt").write_text("old\n")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=repo,
                       check=True)
        (repo / "a.txt").write_text("new\n")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "OPENCODE_CONTINUE_ON_LENGTH": str(max_continuations), "OPENCODE_CONTINUE_RECORD_DIR": str(record),
               **(extra_env or {})}
        proc = subprocess.run(["bash", str(WRAPPER), "--format", "json", "--auto"], cwd=repo, env=env,
                              input=prompt, capture_output=True, timeout=60)
        calls = int((fake_dir / "count").read_text())
        record_file = record / "continuations.json"
        return {"proc": proc, "calls": calls, "fake": fake_dir, "record_dir": record,
                "record": json.loads(record_file.read_text()) if record_file.exists() else None}

    def test_a_clean_stop_is_not_continued(self):
        result = self.run_wrapper([[START, STOP]])
        self.assertEqual((result["proc"].returncode, result["calls"]), (0, 1))
        self.assertEqual(result["record"]["continuations"], 0)
        self.assertEqual((result["fake"] / "stdin_1").read_bytes(), b"fix the bug")  # prompt passes through
        self.assertEqual((result["fake"] / "args_1").read_text().split(), ["run", "--format", "json", "--auto"])

    def test_a_length_stop_continues_the_same_session_with_the_nudge(self):
        result = self.run_wrapper([[START, LENGTH], [START, STOP]])
        proc, fake = result["proc"], result["fake"]
        self.assertEqual((proc.returncode, result["calls"]), (0, 2))
        self.assertEqual((fake / "args_2").read_text().split(), ["run", "-s", "ses_A", "--format", "json", "--auto"])
        self.assertIn(b"hit the output token limit", (fake / "stdin_2").read_bytes())
        self.assertEqual(proc.stdout.decode().splitlines(), [START, LENGTH, START, STOP])
        self.assertIn(b"[continue-on-length] 1/2 session=ses_A", proc.stderr)
        record = result["record"]
        self.assertEqual((record["continuations"], record["final_reason"], record["exit"]), (1, "stop", 0))
        nudge = (fake / "stdin_2").read_bytes()
        self.assertEqual(record["nudge_sha256"], hashlib.sha256(nudge).hexdigest())
        snapshot = (result["record_dir"] / "length_stop_1.patch").read_text()
        self.assertIn("+new", snapshot)  # what the run would have submitted without continuing

    def test_continuations_are_capped(self):
        result = self.run_wrapper([[START, LENGTH]] * 4, max_continuations=2)
        self.assertEqual((result["proc"].returncode, result["calls"]), (0, 3))
        self.assertEqual((result["record"]["continuations"], result["record"]["final_reason"]), (2, "length"))

    def test_exit_codes_pass_through(self):
        result = self.run_wrapper([[START, LENGTH]], exits={1: 1})
        self.assertEqual((result["proc"].returncode, result["calls"]), (1, 1))
        result = self.run_wrapper([[START, LENGTH], [START, STOP]], exits={2: 3})
        self.assertEqual((result["proc"].returncode, result["calls"]), (3, 2))

    def test_escaped_events_inside_tool_output_are_ignored(self):
        result = self.run_wrapper([[START, ESCAPED]])
        self.assertEqual(result["calls"], 1)

    def test_a_continuation_that_never_starts_is_retried_then_abandoned(self):
        result = self.run_wrapper([[START, LENGTH], [], []], sleeps={2: 30, 3: 30},
                                  extra_env={"OPENCODE_CONTINUE_STARTUP_TIMEOUT": "1",
                                             "OPENCODE_CONTINUE_STARTUP_RETRIES": "1"})
        self.assertEqual((result["proc"].returncode, result["calls"]), (0, 3))
        self.assertEqual((result["record"]["startup_stalls"], result["record"]["final_reason"]), (2, "length"))

    def test_continued_sessions_are_counted_by_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "stdout.jsonl").write_text("\n".join([START, LENGTH, START, STOP]) + "\n")
            usage = token_usage.opencode_usage(tmp)
            self.assertEqual((usage["requests"], usage["final_record"]), (2, True))
            self.assertEqual(usage["output_tokens"], 16000 + 5 + 7)


class AdapterTests(unittest.TestCase):
    """run_infer.sh + agents/opencode.sh with a fake runner."""

    def run_infer(self, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "bin"
            fake.mkdir()
            (fake / "python").write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            (fake / "python").chmod(0o755)
            binary = Path(tmp) / "opencode"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            config_file = Path(tmp) / "home/config/opencode/opencode.json"
            config_file.parent.mkdir(parents=True)
            config_file.write_text("{}")
            (Path(tmp) / "home/data").mkdir()
            env = {k: v for k, v in os.environ.items()
                   if k not in ("RIPGREP_BIN", "OPENCODE_CONTINUE_ON_LENGTH", "PYTHON_ENV")}
            env.update({"PATH": f"{fake}:{os.environ['PATH']}", "OPENCODE_BIN": str(binary),
                        "OPENCODE_CONFIG": str(config_file), "OPENCODE_VERSION": "", **extra})
            return subprocess.run(["bash", str(ROOT / "patcheval/exp_agent/run_infer.sh"), "opencode", "label"],
                                  env=env, capture_output=True, text=True)

    @staticmethod
    def values(out, flag):
        lines = out.splitlines()
        return [lines[i + 1] for i, line in enumerate(lines) if line == flag]

    def test_defaults_mount_ripgrep_keep_python_env_and_the_legacy_command(self):
        proc = self.run_infer()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        mounts = self.values(proc.stdout, "--mount")
        self.assertIn(f"{ROOT}/third_party/ripgrep/15.1.0/rg:/usr/local/bin/rg:ro", mounts)
        self.assertIn("--python-env", proc.stdout.splitlines())
        self.assertEqual(self.values(proc.stdout, "--agent-command"), [LEGACY_COMMAND])

    def test_features_can_be_turned_off(self):
        proc = self.run_infer(RIPGREP_BIN="none", PYTHON_ENV="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(any("/usr/local/bin/rg" in m for m in self.values(proc.stdout, "--mount")))
        self.assertIn("--no-python-env", proc.stdout.splitlines())
        self.assertEqual(self.run_infer(PYTHON_ENV="maybe").returncode, 2)
        self.assertEqual(self.run_infer(OPENCODE_CONTINUE_ON_LENGTH="x").returncode, 2)

    def test_continue_on_length_mounts_the_wrapper(self):
        proc = self.run_infer(OPENCODE_CONTINUE_ON_LENGTH="2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"{WRAPPER}:/opt/agent-tools/opencode-continue.sh:ro", self.values(proc.stdout, "--mount"))
        command = self.values(proc.stdout, "--agent-command")[0]
        self.assertIn("XDG_DATA_HOME=/tmp/opencode-data OPENCODE_CONTINUE_ON_LENGTH=2 ", command)
        self.assertTrue(command.endswith("bash /opt/agent-tools/opencode-continue.sh --format json --auto "
                                         "< {prompt_file}"))
        self.assertIn("< /results/prompt.txt", command.format(prompt_file="/results/prompt.txt", workdir="/w"))

    def test_ripgrep_pin_matches_the_vendored_files(self):
        directory = ROOT / "third_party/ripgrep/15.1.0"
        sums = dict(reversed(line.split("  ")) for line in (directory / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(hashlib.sha256((directory / "rg.xz").read_bytes()).hexdigest(), sums["rg.xz"])
        self.assertIn("rg", sums)
        adapter = (ROOT / "patcheval/exp_agent/agents/opencode.sh").read_text()
        self.assertIn("RIPGREP_PINNED_VERSION=15.1.0", adapter)
        self.assertIn("RIPGREP_VENDORED_BIN=third_party/ripgrep/15.1.0/rg", adapter)
        harness = OmegaConf.to_container(OmegaConf.load(ROOT / "scripts/conf/harness/opencode.yaml"), resolve=False)
        self.assertEqual(harness["ripgrep"], "${repo:}/third_party/ripgrep/15.1.0/rg")


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, python_env=True, prepare=None, agent_files=None):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(run_root=tmp, container_prefix="fixture", agent_command="AGENT",
                                      agent_timeout=60, save_trajectories=True, trajectory_path=[],
                                      python_env=python_env)
            sample = {"cve_id": "CVE-2099-7", "image_url": "fixture", "repo": "https://example/repo"}
            seen = {}

            async def detect(container, sample):
                calls.append("detect")
                return "/workspace/repo"

            async def hide(container, workdir, key):
                calls.append("hide")

            async def prep(container, cve):
                calls.append("prepare")
                if isinstance(prepare, Exception):
                    raise prepare
                return prepare or {"status": "relocated"}

            async def agent(container, workdir, command, work, env, timeout, **watch):
                seen.update(command=command, env=env)
                for name, text in (agent_files or {}).items():
                    (work / name).write_text(text)
                return runner.CommandResult([], 0, "", "", 1)

            async def collect(container, workdir, work):
                (work / "llm.patch").write_text("patch")
                return runner.CommandResult([], 0, "", "", 0)

            with patch.object(runner, "_run", AsyncMock(return_value=runner.CommandResult([], 0, "", "", 0))), \
                 patch.object(runner, "_detect_workdir", side_effect=detect), \
                 patch.object(runner, "_prepare_python_env", side_effect=prep), \
                 patch.object(runner, "_hide_workspace_payload", side_effect=hide), \
                 patch.object(runner, "_run_agent", side_effect=agent), \
                 patch.object(runner, "_collect_patch", side_effect=collect), \
                 patch.object(runner, "_remove_container", AsyncMock()):
                outcome = await runner._run_one(sample, 3, args, asyncio.Semaphore(1), [])
            trajectory = Path(outcome.trajectory_path)
            files = sorted(p.name for p in trajectory.iterdir())
            metadata = json.loads((trajectory / "metadata.json").read_text())
        return outcome, calls, seen, files, metadata

    async def test_python_env_is_prepared_before_hiding_and_sourced_first(self):
        outcome, calls, seen, _, metadata = await self.run_case()
        self.assertEqual(calls, ["detect", "prepare", "hide"])
        self.assertEqual(seen["command"], f". {runner.PYTHON_ENV_FILE}; AGENT")
        self.assertNotIn("CVE", json.dumps(seen["env"]))
        self.assertEqual((outcome.status, outcome.python_env), ("generated", "relocated"))
        self.assertEqual(metadata["python_env_detail"], {"status": "relocated"})

    async def test_no_env_leaves_the_command_and_disabled_skips_preparation(self):
        outcome, _, seen, _, _ = await self.run_case(prepare={"status": "none"})
        self.assertEqual((seen["command"], outcome.python_env), ("AGENT", "none"))
        outcome, calls, seen, _, _ = await self.run_case(python_env=False)
        self.assertEqual((calls, seen["command"], outcome.python_env), (["detect", "hide"], "AGENT", None))

    async def test_failed_preparation_is_a_startup_failure(self):
        outcome, calls, _, _, _ = await self.run_case(prepare=RuntimeError("copy failed"))
        self.assertEqual((outcome.status, outcome.startup_failed), ("failed", True))
        self.assertEqual(calls, ["detect", "prepare"])

    async def test_continuation_records_reach_results_and_trajectory(self):
        record = json.dumps({"max": 2, "continuations": 1, "startup_stalls": 0, "first_exit": 0, "exit": 0,
                             "final_reason": "stop", "nudge_sha256": "x"})
        outcome, _, _, files, metadata = await self.run_case(
            agent_files={"continuations.json": record, "length_stop_1.patch": ""})
        self.assertEqual(outcome.continuations, 1)
        self.assertIn("continuations.json", files)
        self.assertIn("length_stop_1.patch", files)
        self.assertEqual(metadata["continuation_record"]["final_reason"], "stop")

    async def test_prepare_python_env_parses_status_lines(self):
        with patch.object(runner, "_docker_exec", AsyncMock(return_value=runner.CommandResult(
                [], 0, "status=relocated\nseconds=4\nleaks=0\n", "", 1))):
            self.assertEqual(await runner._prepare_python_env("c", "CVE-1"),
                             {"status": "relocated", "seconds": "4", "leaks": "0"})
        with patch.object(runner, "_docker_exec", AsyncMock(return_value=runner.CommandResult([], 0, "", "", 1))):
            self.assertEqual(await runner._prepare_python_env("c", "CVE-1"), {"status": "none"})
        with patch.object(runner, "_docker_exec", AsyncMock(return_value=runner.CommandResult([], 1, "", "x", 1))):
            with self.assertRaises(RuntimeError):
                await runner._prepare_python_env("c", "CVE-1")

    def test_summary_extras_and_parser(self):
        self.assertEqual(runner._summary_extras([{"python_env": None}]), {})
        self.assertEqual(runner._summary_extras([{"python_env": "relocated", "continuations": 1},
                                                 {"python_env": "none", "continuations": 0}]),
                         {"python_env": {"none": 1, "relocated": 1}, "continued_tasks": 1, "continuations": 1})
        parse = runner.build_parser().parse_args
        base = ["--agent-command", "x"]
        self.assertTrue(parse(base).python_env)
        self.assertFalse(parse(base + ["--no-python-env"]).python_env)


class WorkflowTests(unittest.TestCase):
    def test_invalid_feature_settings_are_rejected(self):
        for overrides in (("harness=codex", "+harness.continue_on_length=1"),
                          ("harness=codex", "+harness.ripgrep=/bin/true"),
                          ("harness=opencode", "harness.continue_on_length=-1"),
                          ("harness=opencode", "harness.continue_on_length=true"),
                          ("harness=opencode", "generation.python_env=maybe")):
            with self.assertRaises(ValueError, msg=overrides):
                workflow.validate(config("action=generate", "server.host=127.0.0.1", *overrides))

    def test_agent_environment_is_recorded_and_checked_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = {"RIPGREP_BIN": str(ROOT / "third_party/ripgrep/15.1.0/rg"), "OPENCODE_CONTINUE_ON_LENGTH": "2",
                   "PYTHON_ENV": "true"}
            record = workflow.record_agent_environment(env, tmp)
            self.assertEqual(json.loads((tmp / "agent-environment.json").read_text()), record)
            self.assertEqual(record["continue_wrapper_sha256"],
                             hashlib.sha256(WRAPPER.read_bytes()).hexdigest())
            workflow.check_resume_environment(tmp, record)
            with self.assertRaises(ValueError):
                workflow.check_resume_environment(tmp, {**record, "continue_on_length": 0})
