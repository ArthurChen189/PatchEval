import asyncio
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf
from scripts import run as workflow
from patcheval.exp_agent import patch_agent_runner as runner

ROOT = Path(__file__).resolve().parents[1]


def config(*overrides, hydra=False):
    with initialize_config_dir(version_base='1.3', config_dir=str(ROOT / 'scripts/conf')):
        return compose(config_name='config', overrides=list(overrides), return_hydra_config=hydra)


class ArtifactPathTests(unittest.TestCase):
    def test_default_root_and_separate_runtime(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ['HOME'] = '/tmp/test-home'
            cfg = config(hydra=True)
            root = ROOT / 'patcheval/exp_agent/agent_runs'
            self.assertEqual(Path(cfg.paths.runs), root)
            self.assertTrue(Path(cfg.hydra.run.dir).is_relative_to(root))
            self.assertTrue(Path(cfg.hydra.sweep.dir).is_relative_to(root))
            self.assertEqual(cfg.paths.runtime, '/tmp/test-home/.cache/patcheval/local_llm')
            self.assertFalse(Path(cfg.paths.serving_env).is_relative_to(root))
            self.assertIsNone(cfg.configure.output_dir)

    def test_all_action_artifacts_stay_in_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            cfg = config('server.host=127.0.0.1', 'dry_run=true')
            _, env = workflow.generation_job(cfg, output)
            self.assertEqual(env['OUTPUT_BASE'], str(output / 'generation'))
            self.assertTrue(Path(env['CODEX_CONFIG']).is_relative_to(output / 'harnesses'))
            cfg.action = 'configure'
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.dispatch(cfg, output)
            self.assertEqual(cfg.configure.output_dir, str(output / 'harnesses'))
            self.assertEqual(OmegaConf.load(output / 'resolved.yaml').configure.output_dir,
                             str(output / 'harnesses'))
            cfg.action = 'analyze'
            cfg.analysis.input_dir = 'input-example'
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                workflow.dispatch(cfg, output)
            self.assertIn(str(output / 'analysis'), printed.getvalue())
            cfg.evaluation.run_dir = str(output / 'generation' / 'completed')
            (Path(cfg.evaluation.run_dir) / 'patches').mkdir(parents=True)
            _, jobs = workflow.evaluation_jobs(cfg, output)
            convert, _ = jobs[0]
            self.assertEqual(convert[convert.index('--process-data-path') + 1], str(output / 'eval_inputs/patches.jsonl'))
            evaluate, cwd = jobs[1]
            destination = (cwd / 'evaluation_output' / evaluate[evaluate.index('--output') + 1]).resolve()
            self.assertEqual(destination, output / 'evaluation_output')

    def test_explicit_relative_analysis_and_configure_destinations(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(workflow, 'ROOT', Path(tmp)):
            cfg = config('action=configure', 'configure.output_dir=custom/harnesses',
                         'server.host=127.0.0.1', 'dry_run=true')
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.dispatch(cfg, 'invocation')
            self.assertEqual(cfg.configure.output_dir, str(Path(tmp) / 'custom/harnesses'))
            cfg.action = 'analyze'
            cfg.analysis.input_dir = 'source'
            cfg.analysis.output_dir = 'custom/analysis'
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                workflow.dispatch(cfg, 'invocation')
            self.assertIn(str(Path(tmp) / 'custom/analysis'), printed.getvalue())

    def test_relative_hydra_run_and_sweep_from_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            caller = root / 'caller'
            caller.mkdir()
            for sweep in (False, True):
                destination = root / ('sweep' if sweep else 'single')
                relative = os.path.relpath(destination, ROOT)
                args = [sys.executable, str(ROOT / 'scripts/run.py'), 'action=configure',
                        'dry_run=true', 'server.host=127.0.0.1']
                if sweep:
                    args.insert(2, '--multirun')
                    args += [f'hydra.sweep.dir={relative}', 'label=first,second']
                else:
                    args += [f'hydra.run.dir={relative}']
                result = subprocess.run(args, cwd=caller, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                outputs = [destination / '0', destination / '1'] if sweep else [destination]
                for output in outputs:
                    self.assertTrue((output / '.hydra/config.yaml').is_file())
                    self.assertTrue((output / 'run.log').is_file())
                    resolved = OmegaConf.load(output / 'resolved.yaml')
                    self.assertEqual(resolved.configure.output_dir, str(output / 'harnesses'))
                    self.assertFalse((output / 'harnesses').exists())
            self.assertEqual(list(caller.iterdir()), [])

    def test_direct_runner_default_and_repeated_runs_are_isolated(self):
        self.assertEqual(runner.build_parser().parse_args(['--agent-command', 'true']).output_dir,
                         str(ROOT / 'patcheval/exp_agent/agent_runs'))
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'ROOT', Path(tmp)), \
             patch.object(runner, '_read_json', return_value=[]), \
             patch.object(runner, '_require_dataset_images'), \
             patch.object(runner.time, 'strftime', return_value='same-second'), \
             contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                args = runner.build_parser().parse_args(['--agent-command', 'true',
                                                        '--output-dir', 'custom', '--run-label', 'label'])
                self.assertEqual(asyncio.run(runner._main(args)), 0)
            runs = list((Path(tmp) / 'custom').glob('*-label'))
            self.assertEqual(len(runs), 2)
            self.assertTrue(all((run / 'summary.json').is_file() for run in runs))

    def test_legacy_evaluation_wrapper_preserves_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'python'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
            executable.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'RUNS_DIR': 'custom-runs', 'EVALUATION_OUTPUT_DIR': 'custom-evaluation',
                   'MAX_WORKERS': '7'}
            result = subprocess.run(['bash', str(ROOT / 'patcheval/exp_agent/run_eval.sh'),
                                     'label', 'custom-input'], cwd=tmp, env=env, text=True, capture_output=True, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], str(ROOT))
            self.assertIn('paths.runs=custom-runs', lines)
            self.assertIn('hydra.run.dir=custom-evaluation', lines)
            self.assertIn('evaluation.run_dir=custom-input', lines)
            self.assertIn('evaluation.max_workers=7', lines)

    def test_temp_helper_default_and_relative_override_from_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'bin').mkdir()
            caller = root / 'caller'
            caller.mkdir()
            (root / 'temp_run_script.sh').write_text((ROOT / 'temp_run_script.sh').read_text())
            for name in ('docker', 'uv'):
                executable = root / 'bin' / name
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
            (root / 'scripts/run.sh').write_text("""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CAPTURE"
for arg in "$@"; do
  case "$arg" in
    action=*) action="${arg#*=}" ;;
    hydra.run.dir=*) job_dir="${arg#*=}" ;;
  esac
done
if [[ "$action" == generate ]]; then
  mkdir -p "$job_dir/generation/example/patches"
  printf '{}' > "$job_dir/generation/example/summary.json"
fi
""")
            env = {**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
                   'CAPTURE': str(root / 'calls')}
            env.pop('RUNS_DIR', None)
            for override in (None, 'relative-runs'):
                if override:
                    env['RUNS_DIR'] = override
                (root / 'calls').write_text('')
                subprocess.run(['bash', str(root / 'temp_run_script.sh'), 'smoke'],
                               cwd=caller, env=env, capture_output=True, text=True, check=True)
                calls = (root / 'calls').read_text().splitlines()
                expected = root / (override or 'patcheval/exp_agent/agent_runs')
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(f'paths.runs={expected}' in call for call in calls))
                self.assertIn(f'evaluation.run_dir={expected}/hydra/', calls[2])
                self.assertEqual(len(list((expected / 'hydra').iterdir())), 1)
            self.assertEqual(list(caller.iterdir()), [])
