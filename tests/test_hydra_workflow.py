import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from hydra import compose, initialize_config_dir
from scripts import run as workflow

ROOT = Path(__file__).resolve().parents[1]


def config(*overrides):
    with initialize_config_dir(version_base='1.3', config_dir=str(ROOT / 'scripts/conf')):
        return compose(config_name='config', overrides=list(overrides))


class HydraWorkflowTests(unittest.TestCase):
    def test_groups_and_model_overrides_drive_both_harnesses(self):
        cfg = config('experiment=smoke', 'harness=opencode', 'model.path=local-model',
                     'model.context_length=32768', 'model.output_tokens=4096', 'server.host=127.0.0.1')
        self.assertEqual(cfg.generation.limit, 1)
        self.assertEqual(cfg.label, 'local_opencode_smoke')
        self.assertEqual(cfg.model.served_name, 'local-model')
        with tempfile.TemporaryDirectory() as tmp:
            cfg.action = 'configure'
            cfg.configure.output_dir = str(Path(tmp) / 'homes with spaces')
            workflow.dispatch(cfg, Path(tmp) / 'record')
            home = Path(cfg.configure.output_dir)
            codex = tomllib.loads((home / 'codex/config.toml').read_text())
            opencode = json.loads((home / 'opencode/config/opencode/opencode.json').read_text())
            self.assertEqual(codex['model'], cfg.model.served_name)
            self.assertEqual(codex['model_auto_compact_token_limit'], 28672)
            self.assertEqual(opencode['provider']['sglang']['models']['local-model']['limit'],
                             {'context': 32768, 'output': 4096})
            self.assertTrue((Path(tmp) / 'record/resolved.yaml').exists())

    def test_serving_command_preserves_literal_arguments(self):
        cfg = config('server.host=127.0.0.1', 'model.context_length=65536')
        cfg.model.path = '/models/path with spaces'
        cfg.server.extra_args = ['--json-model-override-args', '{"literal":"$(touch /tmp/not-executed)"}']
        command = workflow.serve_command(cfg)
        self.assertEqual(command[command.index('--model-path') + 1], cfg.model.path)
        self.assertEqual(command[-2:], list(cfg.server.extra_args))
        self.assertEqual(command[command.index('--context-length') + 1], '65536')

    def test_generate_uses_config_instead_of_ambient_runner_settings(self):
        cfg = config('action=generate', 'experiment=smoke', 'harness=opencode',
                     'generation.timeout=42', 'server.host=127.0.0.1')
        cfg.harness.binary = '/bin/true'
        with tempfile.TemporaryDirectory(prefix='generation paths ') as tmp:
            cfg.paths.runs = tmp
            with patch.dict(os.environ, {'LIMIT': '230', 'CONCURRENCY': '99'}):
                with patch.object(workflow.subprocess, 'run') as execute:
                    workflow.dispatch(cfg, Path(tmp) / 'hydra job')
            args, kwargs = execute.call_args
            self.assertEqual(args[0][-2:], ['opencode', 'local_opencode_smoke'])
            self.assertEqual(kwargs['env']['LIMIT'], '1')
            self.assertEqual(kwargs['env']['CONCURRENCY'], '1')
            self.assertEqual(kwargs['env']['AGENT_TIMEOUT'], '42')
            self.assertEqual(kwargs['env']['SAVE_TRAJECTORIES'], 'true')
            cfg.generation.save_trajectories = False
            cfg.dry_run = True
            _, disabled_env = workflow.generation_job(cfg, Path(tmp))
            self.assertEqual(disabled_env['SAVE_TRAJECTORIES'], 'false')
            self.assertEqual(kwargs['env']['OUTPUT_BASE'], str(Path(tmp) / 'hydra job/generation'))
            self.assertTrue(Path(kwargs['env']['OPENCODE_CONFIG']).is_file())
            self.assertEqual(kwargs['cwd'], ROOT)

    def test_explicit_harness_config_does_not_need_docker_or_rendering(self):
        cfg = config('action=generate', 'harness=traecli')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'custom.traecli.toml'
            source.touch()
            cfg.harness.binary = '/bin/true'
            cfg.harness.config = str(source)
            with patch.object(workflow, 'bridge_host', side_effect=AssertionError('unexpected Docker call')):
                command, env = workflow.generation_job(cfg, Path(tmp))
            self.assertEqual(env['TRAE_CONFIG'], str(source))
            self.assertEqual(command[-2], 'traecli')

    def test_latest_run_honors_custom_output_base_and_ignores_incomplete(self):
        cfg = config('action=evaluate', 'label=example')
        with tempfile.TemporaryDirectory() as tmp:
            cfg.paths.runs = tmp
            for name, completed, timestamp in [('older-example', True, 10), ('newer-example', True, 20), ('unfinished-example', False, 30)]:
                path = Path(tmp) / name
                (path / 'patches').mkdir(parents=True)
                if completed:
                    (path / 'summary.json').write_text('{}')
                os.utime(path, (timestamp, timestamp))
            self.assertEqual(workflow.find_run(cfg).name, 'newer-example')
            run, jobs = workflow.evaluation_jobs(cfg, Path(tmp) / 'evaluation job')
            convert, _ = jobs[0]
            command, cwd = jobs[1]
            self.assertEqual(convert[convert.index('--runner-output') + 1], str(run))
            self.assertEqual(command[command.index('--max_workers') + 1], '1')
            output = (cwd / 'evaluation_output' / command[command.index('--output') + 1]).resolve()
            self.assertEqual(output, Path(tmp) / 'evaluation job/evaluation_output')
            cfg.evaluation.run_dir = str(Path(tmp) / 'older-example')
            self.assertEqual(workflow.find_run(cfg).name, 'older-example')

    def test_latest_run_supports_hydra_multirun_layout(self):
        cfg = config('action=evaluate', 'label=sweep')
        with tempfile.TemporaryDirectory() as tmp:
            cfg.paths.runs = tmp
            run = Path(tmp) / 'hydra/multirun/stamp/0/generation/stamp-sweep'
            (run / 'patches').mkdir(parents=True)
            (run / 'summary.json').write_text('{}')
            self.assertEqual(workflow.find_run(cfg), run)

    def test_no_matching_run_and_invalid_values_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config('action=evaluate')
            cfg.paths.runs = tmp
            with self.assertRaisesRegex(ValueError, 'No completed'):
                workflow.find_run(cfg)
        for override in ('generation.concurrency=0', 'generation.limit=0', 'server.port=70000',
                         'model.output_tokens=999999', 'generation.save_trajectories=invalid', 'label=../unsafe', 'check.protocol=invalid'):
            with self.subTest(override=override), self.assertRaises(ValueError):
                workflow.validate(config(override))

    def test_failed_runner_exit_is_not_swallowed(self):
        cfg = config('action=generate')
        with patch.object(workflow.subprocess, 'run', side_effect=subprocess.CalledProcessError(7, ['bash'])):
            with self.assertRaises(subprocess.CalledProcessError) as exc:
                workflow.run_command(['bash'], {}, cfg)
            self.assertEqual(exc.exception.returncode, 7)

    def test_conversion_failure_stops_evaluation(self):
        cfg = config('action=evaluate')
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'generation'
            (run / 'patches').mkdir(parents=True)
            cfg.evaluation.run_dir = str(run)
            with patch.object(workflow.subprocess, 'run', side_effect=subprocess.CalledProcessError(2, ['convert'])) as execute:
                with self.assertRaises(subprocess.CalledProcessError):
                    workflow.dispatch(cfg, Path(tmp) / 'evaluation job')
            self.assertEqual(execute.call_count, 1)
            self.assertIn('process_data.py', execute.call_args.args[0][1])

    def test_dry_run_does_not_install_or_launch_or_write_harness_configs(self):
        cfg = config('action=generate', 'dry_run=true', 'server.host=127.0.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(workflow.subprocess, 'run', side_effect=AssertionError('unexpected execution')):
                with contextlib.redirect_stdout(io.StringIO()):
                    workflow.dispatch(cfg, Path(tmp))
            self.assertFalse((Path(tmp) / 'harnesses').exists())
            self.assertTrue((Path(tmp) / 'resolved.yaml').exists())

    def test_setup_is_isolated_and_does_not_discover_docker(self):
        cfg = config('action=setup')
        with tempfile.TemporaryDirectory() as tmp:
            cfg.paths.runtime = tmp
            with patch.object(workflow, 'bridge_host', side_effect=AssertionError('unexpected Docker call')):
                with patch.object(workflow.subprocess, 'run') as execute:
                    workflow.dispatch(cfg, Path(tmp) / 'record')
            self.assertEqual(execute.call_count, 2)
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertIn('--managed-python', commands[0])
            self.assertIn(str(Path(tmp) / 'venv/bin/python'), commands[1])
            self.assertEqual(commands[1][-1], 'sglang==0.5.19')

    def test_config_inspection_works_from_another_directory_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/run.py'), '--cfg', 'job', '--resolve',
                                     'harness=opencode', 'experiment=smoke'], cwd=tmp, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('local_opencode_smoke', result.stdout)
            self.assertIn(str(ROOT / 'patcheval/datasets/patcheval_verified.json'), result.stdout)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_python_aliases_share_config_search_path_and_accept_hydra_flags(self):
        for script, action in [('check_server.py', 'check'), ('configure_harnesses.py', 'configure')]:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/infer' / script),
                                         '--cfg', 'job', 'experiment=smoke'],
                                        cwd=tmp, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'action: {action}', result.stdout)

    def test_unknown_override_is_rejected(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/run.py'), '--cfg', 'job',
                                 'generation.concurency=2'], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('generation.concurency', result.stderr)


if __name__ == '__main__':
    unittest.main()
