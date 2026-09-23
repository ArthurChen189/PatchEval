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
from omegaconf import OmegaConf
from scripts import run as workflow

ROOT = Path(__file__).resolve().parents[1]


def config(*overrides):
    with initialize_config_dir(version_base='1.3', config_dir=str(ROOT / 'scripts/conf')):
        return compose(config_name='config', overrides=list(overrides))


class HydraWorkflowTests(unittest.TestCase):
    def test_analysis_dispatch_is_separate_and_does_not_contact_docker(self):
        cfg = config('action=analyze', 'analysis.input_dir=agent_runs/example')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('scripts.infer.analyze_trajectories.analyze', return_value={'completed_tasks': 1}) as normalize:
                with patch.object(workflow, 'bridge_host', side_effect=AssertionError('unexpected Docker call')):
                    workflow.dispatch(cfg, Path(tmp))
            normalize.assert_called_once_with(ROOT / 'agent_runs/example', Path(tmp) / 'analysis')
            cfg.dry_run = True
            with patch('scripts.infer.analyze_trajectories.analyze') as normalize:
                workflow.dispatch(cfg, Path(tmp))
                normalize.assert_not_called()

    def test_groups_and_model_overrides_drive_both_harnesses(self):
        cfg = config('experiment=smoke', 'harness=opencode', 'model.path=local-model',
                     'model.context_length=32768', 'model.output_tokens=4096', 'server.host=127.0.0.1')
        self.assertEqual(cfg.generation.limit, 5)
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
            self.assertEqual(opencode['provider']['vllm']['models']['local-model']['limit'],
                             {'context': 32768, 'output': 4096})
            self.assertTrue((Path(tmp) / 'record/resolved.yaml').exists())

    def test_invocation_directories_are_named_after_their_work(self):
        runs = '/runs/hydra'
        cases = {
            ('generate', 'codex', 'my_run', None, None): 'generate-codex-my_run',
            ('generate', 'opencode', 'x', None, f'{runs}/gen-opencode-full-1-abc'): 'resume-gen-opencode-full-1-abc',
            ('evaluate', 'codex', 'local_codex', f'{runs}/gen-opencode-full-1-abc/generation/sample_0/r', None):
                'evaluate-gen-opencode-full-1-abc',
            ('evaluate', 'codex', 'local_codex', f'{runs}/multirun/stamp/0/generation/run', None): 'evaluate-stamp',
            ('evaluate', 'codex', 'my run', None, None): 'evaluate-my_run',
            ('check', 'opencode', 'l', None, None): 'check-opencode',
            ('serve', 'codex', 'l', None, None): 'serve',
        }
        for args, expected in cases.items():
            self.assertEqual(workflow.run_name(*args), expected)
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/run.py'), 'action=check', 'dry_run=true',
                                     'harness=opencode', 'server.host=127.0.0.1', f'paths.runs={tmp}'],
                                    capture_output=True, text=True, check=True)
            cfg = config('harness=opencode')
            group = workflow.group_name(cfg.model.served_name, 'opencode', cfg.model.output_tokens)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [group], result.stdout)
            names = [p.name for p in (Path(tmp) / group).iterdir()]
        self.assertEqual(len(names), 1, result.stdout)
        self.assertRegex(names[0], r'^\d{8}_\d{6}_\d{6}-check-opencode$')

    def test_invocations_are_grouped_by_model_harness_and_output_cap(self):
        self.assertEqual(workflow.cap_label(16000), '16k')
        self.assertEqual(workflow.cap_label(8192), '8k')
        self.assertEqual(workflow.cap_label(12345), '12345')
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            group = workflow.run_group
            codex = 'Qwen3.8-27B_Codex_Max-output-token=16k'
            self.assertEqual(group(tmp, 'generate', 'Qwen/Qwen3.8-27B', 'codex', 16000), codex)
            self.assertEqual(group(tmp, 'check', 'Qwen/Qwen3.5-9B', 'opencode', 8192),
                             'Qwen3.5-9B_OpenCode_Max-output-token=8k')
            self.assertEqual(group(tmp, 'serve', 'Qwen/Qwen3.8-27B', 'codex', 16000), 'Qwen3.8-27B_vLLM-serving')
            # Evaluations and resumes join the scored run's group, whatever `harness` says.
            invocation = runs / 'Qwen3.8-27B_OpenCode_Max-output-token=16k/stamp-generate'
            (invocation / 'generation/sample_0/run').mkdir(parents=True)
            (invocation / 'resolved.yaml').write_text('harness:\n  name: opencode\n')
            sample = str(invocation / 'generation/sample_0/run')
            self.assertEqual(group(tmp, 'evaluate', 'Qwen/Qwen3.8-27B', 'codex', 16000, sample),
                             invocation.parent.name)
            self.assertEqual(group(tmp, 'generate', 'Qwen/Qwen3.8-27B', 'codex', 16000, None, str(invocation)),
                             invocation.parent.name)
            self.assertEqual(workflow.run_name('evaluate', 'codex', 'l', sample), 'evaluate-stamp-generate')
            # Legacy hydra/ runs: the group comes from the run's own resolved.yaml.
            legacy = runs / 'hydra/old-run'
            legacy.mkdir(parents=True)
            OmegaConf.save(OmegaConf.create({'harness': {'name': 'opencode'},
                                             'model': {'served_name': 'Qwen/Qwen3.5-9B', 'output_tokens': 8192}}),
                           legacy / 'resolved.yaml')
            self.assertEqual(group(tmp, 'evaluate', 'Qwen/Qwen3.8-27B', 'codex', 16000, str(legacy)),
                             'Qwen3.5-9B_OpenCode_Max-output-token=8k')
            # Label lookup finds runs inside group folders.
            run = invocation / 'generation/sample_0/2026-grouped'
            (run / 'patches').mkdir(parents=True)
            (run / 'summary.json').write_text('{}')
            (invocation / 'resolved.yaml').write_text('generation:\n  samples: 1\n')
            cfg = config('action=evaluate', 'label=grouped')
            cfg.paths.runs = tmp
            self.assertEqual(workflow.find_runs(cfg), [run])

    def test_values_containing_equals_are_quoted_for_hydra(self):
        quote = workflow.quote_override
        self.assertEqual(quote('evaluation.run_dir=/r/Qwen_Codex_Max-output-token=16k/gen'),
                         'evaluation.run_dir="/r/Qwen_Codex_Max-output-token=16k/gen"')
        self.assertEqual(quote('label=plain'), 'label=plain')
        self.assertEqual(quote('server.extra_args=[--x=y]'), 'server.extra_args=[--x=y]')
        self.assertEqual(quote("a='b=c'"), "a='b=c'")
        self.assertEqual(quote('--cfg'), '--cfg')
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / 'Qwen3.8-27B_Codex_Max-output-token=16k'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/run.py'), '--cfg', 'job',
                                     f'evaluation.run_dir={run_dir}'], capture_output=True, text=True, check=True)
        self.assertIn(f'run_dir: {run_dir}', result.stdout)

    def test_serving_command_preserves_literal_arguments(self):
        cfg = config('server.host=127.0.0.1', 'model.context_length=65536')
        cfg.model.path = '/models/path with spaces'
        cfg.server.extra_args = ['--hf-overrides', '{"literal":"$(touch /tmp/not-executed)"}']
        command = workflow.serve_command(cfg)
        self.assertEqual(command[2], cfg.model.path)
        self.assertEqual(command[-2:], list(cfg.server.extra_args))
        self.assertEqual(command[command.index('--max-model-len') + 1], '65536')
        self.assertEqual(command[command.index('--tensor-parallel-size') + 1], '1')
        self.assertEqual(command[command.index('--data-parallel-size') + 1], '8')

    def test_vllm_defaults_and_parallel_overrides(self):
        cfg = config('server.host=127.0.0.1')
        command = workflow.serve_command(cfg)
        self.assertEqual(command[:3], [str(workflow.absolute(cfg.paths.serving_env) / 'bin/vllm'),
                                     'serve', 'Qwen/Qwen3.8-27B'])
        for flag, value in {'--max-model-len': '262144', '--gpu-memory-utilization': '0.85',
                            '--max-num-seqs': '8', '--reasoning-parser': 'qwen3',
                            '--tool-call-parser': 'qwen3_xml'}.items():
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn('--enable-auto-tool-choice', command)
        generation = json.loads(command[command.index('--override-generation-config') + 1])
        self.assertEqual(generation, {'max_new_tokens': 16000})
        self.assertEqual(workflow.serving_environment(cfg)['CUDA_VISIBLE_DEVICES'], '0,1,2,3,4,5,6,7')
        cfg.server.tensor_parallel, cfg.server.data_parallel = 2, 4
        cfg.model.tool_call_parser = None
        command = workflow.serve_command(cfg)
        self.assertEqual(command[command.index('--tensor-parallel-size') + 1], '2')
        self.assertEqual(command[command.index('--data-parallel-size') + 1], '4')
        self.assertNotIn('--enable-auto-tool-choice', command)

    def test_lossless_acceleration_flags(self):
        cfg = config('server.host=127.0.0.1')
        command = workflow.serve_command(cfg)
        self.assertEqual(json.loads(command[command.index('--speculative-config') + 1]),
                         {'method': 'mtp', 'num_speculative_tokens': 3})
        self.assertIn('--enable-prefix-caching', command)
        self.assertIn('--enable-chunked-prefill', command)
        self.assertEqual(config('experiment=full').generation.concurrency, 48)
        cfg = config('server.host=127.0.0.1', 'server.speculative=null',
                     'server.prefix_caching=false', 'server.chunked_prefill=false')
        command = workflow.serve_command(cfg)
        self.assertNotIn('--speculative-config', command)
        self.assertIn('--no-enable-prefix-caching', command)
        self.assertIn('--no-enable-chunked-prefill', command)
        for overrides in (["server.extra_args=['--speculative-config={}']"], ["server.extra_args=[--max-num-seqs,'4']"],
                          ['server.prefix_caching=maybe'], ['server.speculative.num_speculative_tokens=0']):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                workflow.validate(config('server.host=127.0.0.1', *overrides))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            workflow.validate(config('action=generate', 'generation.concurrency=65'))
        self.assertIn('exceeds', err.getvalue())

    def test_uniform_generation_defaults(self):
        cfg = config('server.host=127.0.0.1')
        self.assertEqual((cfg.model.output_tokens, cfg.model.temperature, cfg.generation.timeout,
                          cfg.generation.samples), (16000, None, 2400, 4))
        cfg.model.temperature = 0.6
        command = workflow.serve_command(cfg)
        self.assertEqual(json.loads(command[command.index('--override-generation-config') + 1]),
                         {'max_new_tokens': 16000, 'temperature': 0.6})
        for overrides in (['model.temperature=-0.5'], ['model.temperature=true'], ['generation.samples=0'],
                          ["server.extra_args=[--override-generation-config,'{}']"],
                          ["server.extra_args=['--generation-config=vllm']"]):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                workflow.validate(config('server.host=127.0.0.1', *overrides))

    def test_serving_dry_runs_never_execute(self):
        for action in ('setup', 'serve'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                cfg = config(f'action={action}', 'dry_run=true', 'server.host=127.0.0.1')
                cfg.paths.runtime = tmp
                with patch.object(workflow.subprocess, 'run', side_effect=AssertionError('execution')), \
                     patch.object(workflow.subprocess, 'check_output', side_effect=AssertionError('probe')), \
                     patch.object(workflow.os, 'execvpe', side_effect=AssertionError('launch')), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    workflow.dispatch(cfg, Path(tmp) / 'record')
                self.assertIn('vllm', output.getvalue())
                self.assertFalse((Path(tmp) / 'venv-vllm').exists())

    def test_serve_records_version_and_executes_with_environment(self):
        cfg = config('action=serve', 'server.host=127.0.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            cfg.paths.runtime = tmp
            binary_dir = Path(tmp) / 'venv-vllm/bin'
            binary_dir.mkdir(parents=True)
            (binary_dir / 'python').touch()
            (binary_dir / 'vllm').touch()
            (binary_dir / 'vllm').chmod(0o755)
            with patch.object(workflow.shutil, 'which', return_value='/usr/bin/tool'), \
                 patch.object(workflow.subprocess, 'run') as execute, \
                 patch.object(workflow.subprocess, 'check_output', return_value='0.29.0\n'), \
                 patch.object(workflow.logging, 'shutdown'), \
                 patch.object(workflow.os, 'execvpe') as launch:
                workflow.dispatch(cfg, Path(tmp) / 'record')
            self.assertIn('import vllm', execute.call_args.args[0][-1])
            self.assertEqual(json.loads((Path(tmp) / 'record/server-version.json').read_text()),
                             {'vllm': '0.29.0'})
            current = json.loads((Path(tmp) / 'current-server.json').read_text())
            self.assertEqual((current['vllm'], current['argv'][0]), ('0.29.0', 'serve'))
            self.assertIn('--speculative-config', current['argv'])
            self.assertEqual(launch.call_args.args[0], str(binary_dir / 'vllm'))
            self.assertEqual(launch.call_args.args[2]['CUDA_VISIBLE_DEVICES'], '0,1,2,3,4,5,6,7')

    def test_serve_requires_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config('action=serve', 'server.host=127.0.0.1')
            cfg.paths.runtime = tmp
            with self.assertRaisesRegex(ValueError, 'action=setup'):
                workflow.dispatch(cfg, Path(tmp) / 'record')

    def test_shell_serving_aliases_forward_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'infer').mkdir()
            (root / 'run.sh').write_text('printf "%s\\n" "$@"\n')
            for name in ('serve_vllm.sh',):
                (root / 'infer' / name).write_text((ROOT / 'scripts/infer' / name).read_text())
            for name in ('serve_vllm.sh',):
                result = subprocess.run(['bash', str(root / 'infer' / name), 'serve',
                                         'dry_run=true', 'server.gpu=0,1'], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.splitlines(), ['dry_run=true', 'server.gpu=0,1', 'action=serve'])
                self.assertEqual(result.stderr, '')

    def test_opencode_standard_install_fallback_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            installed = home / '.opencode/bin/opencode'
            installed.parent.mkdir(parents=True)
            installed.touch()
            installed.chmod(0o755)
            # The ~/.opencode fallback applies to an explicitly unpinned bare name.
            cfg = config('harness=opencode', 'server.host=127.0.0.1', 'harness.binary=opencode')
            with patch.object(workflow.Path, 'home', return_value=home), \
                 patch.object(workflow.shutil, 'which', return_value=None):
                _, env = workflow.generation_job(cfg, home / 'job')
                self.assertEqual(env['OPENCODE_BIN'], str(installed))
                cfg.dry_run = True
                _, env = workflow.generation_job(cfg, home / 'preview')
                self.assertEqual(env['OPENCODE_BIN'], str(installed))
                cfg.dry_run = False
                cfg.harness.binary = str(home / 'explicit-missing')
                with self.assertRaisesRegex(ValueError, 'Harness executable not found'):
                    workflow.generation_job(cfg, home / 'missing')
                cfg.harness.binary = 'custom-opencode'
                with self.assertRaisesRegex(ValueError, 'Harness executable not found'):
                    workflow.generation_job(cfg, home / 'custom')
                cfg.harness.binary = 'opencode'
                installed.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'Harness executable not found'):
                    workflow.generation_job(cfg, home / 'not-executable')
            cfg.dry_run = True
            with patch.object(workflow.shutil, 'which', return_value='/preferred/opencode'):
                _, env = workflow.generation_job(cfg, home / 'path-preview')
                self.assertEqual(env['OPENCODE_BIN'], '/preferred/opencode')
            with patch.dict(os.environ, {'OPENCODE_BIN': '/custom/opencode'}):
                cfg = config('harness=opencode', 'dry_run=true', 'server.host=127.0.0.1')
                _, env = workflow.generation_job(cfg, home / 'env-preview')
                self.assertEqual(env['OPENCODE_BIN'], '/custom/opencode')

    def test_generate_uses_config_instead_of_ambient_runner_settings(self):
        cfg = config('action=generate', 'experiment=smoke', 'harness=opencode',
                     'generation.timeout=42', 'server.host=127.0.0.1')
        cfg.harness.binary = '/bin/true'
        with tempfile.TemporaryDirectory(prefix='generation paths ') as tmp:
            cfg.paths.runs = tmp
            with patch.dict(os.environ, {'LIMIT': '230', 'CONCURRENCY': '99'}):
                with patch.object(workflow.subprocess, 'run') as execute, \
                     patch.object(workflow.subprocess, 'check_output', return_value='1.18.31\n'):
                    workflow.dispatch(cfg, Path(tmp) / 'hydra job')
            self.assertEqual(execute.call_count, 4)
            self.assertEqual([call.kwargs['env']['OUTPUT_BASE'] for call in execute.call_args_list],
                             [str(Path(tmp) / f'hydra job/generation/sample_{i}') for i in range(4)])
            cfg.generation.samples = 1
            with patch.object(workflow.subprocess, 'run') as execute, \
                 patch.object(workflow.subprocess, 'check_output', return_value='1.18.31\n'):
                workflow.dispatch(cfg, Path(tmp) / 'single job')
            args, kwargs = execute.call_args
            self.assertEqual(args[0][-2:], ['opencode', 'local_opencode_smoke'])
            self.assertEqual(kwargs['env']['LIMIT'], '5')
            self.assertEqual(kwargs['env']['CONCURRENCY'], '8')
            self.assertEqual(kwargs['env']['AGENT_TIMEOUT'], '42')
            self.assertEqual(kwargs['env']['SAVE_TRAJECTORIES'], 'true')
            cfg.generation.save_trajectories = False
            cfg.dry_run = True
            _, disabled_env = workflow.generation_job(cfg, Path(tmp))
            self.assertEqual(disabled_env['SAVE_TRAJECTORIES'], 'false')
            self.assertEqual(kwargs['env']['OUTPUT_BASE'], str(Path(tmp) / 'single job/generation'))
            self.assertTrue(Path(kwargs['env']['OPENCODE_CONFIG']).is_file())
            # The adapter re-checks the same pin as record_harness_version.
            self.assertEqual(kwargs['env']['OPENCODE_VERSION'], '1.18.31')
            self.assertEqual(kwargs['cwd'], ROOT)
            record = json.loads((Path(tmp) / 'single job/harness-version.json').read_text())
            self.assertEqual((record['harness'], record['opencode_version_validated']), ('opencode', '1.18.31'))

    def test_codex_is_pinned_to_the_vendored_release(self):
        cfg = config('harness=codex')
        self.assertEqual(str(cfg.harness.version), '0.155.0')
        if 'CODEX_BIN' not in os.environ:
            self.assertEqual(cfg.harness.binary,
                             str(ROOT / 'third_party/codex/0.155.0/codex-x86_64-unknown-linux-musl'))
        archive = ROOT / 'third_party/codex/0.155.0/codex-x86_64-unknown-linux-musl.xz'
        sums = dict(reversed(line.split('  ')) for line in
                    (archive.parent / 'SHA256SUMS').read_text().splitlines())
        import hashlib
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), sums[archive.name])
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'codex'
            for reported, fails in (('codex-cli 0.155.0', False), ('codex-cli 0.156.0', True)):
                binary.write_text(f'#!/bin/sh\necho {reported}\n')
                binary.chmod(0o755)
                with self.subTest(reported=reported):
                    if fails:
                        with self.assertRaisesRegex(ValueError, 'pinned to 0.155.0'):
                            workflow.record_harness_version(cfg, str(binary), Path(tmp))
                    else:
                        record = workflow.record_harness_version(cfg, str(binary), Path(tmp))
                        self.assertEqual(record['pinned_version'], '0.155.0')

    def test_vendored_binary_is_extracted_and_verified(self):
        import hashlib, lzma
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'tool'
            data = b'#!/bin/sh\necho tool 1.0.0\n'
            packed = lzma.compress(data)
            (Path(tmp) / 'tool.xz').write_bytes(packed)
            (Path(tmp) / 'SHA256SUMS').write_text(f"{hashlib.sha256(packed).hexdigest()}  tool.xz\n"
                                                  f"{hashlib.sha256(data).hexdigest()}  tool\n")
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.ensure_vendored_binary(binary)
            self.assertEqual(binary.read_bytes(), data)
            self.assertTrue(os.access(binary, os.X_OK))
            # An already-extracted binary is re-verified on every run.
            binary.write_bytes(b'#!/bin/sh\necho tool 1.0.0 replaced\n')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                workflow.ensure_vendored_binary(binary)
            binary.unlink()
            (Path(tmp) / 'tool.xz').write_bytes(lzma.compress(b'tampered'))
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                workflow.ensure_vendored_binary(binary)
            self.assertFalse(binary.exists())

    def test_harness_version_is_recorded_and_mismatch_warned(self):
        cfg = config('action=generate', 'harness=opencode', 'server.host=127.0.0.1')
        self.assertEqual(str(cfg.harness.version), '1.18.31')
        if 'OPENCODE_BIN' not in os.environ:
            self.assertEqual(cfg.harness.binary, str(ROOT / 'third_party/opencode/1.18.31/opencode-linux-x64'))
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'opencode'
            binary.write_text('#!/bin/sh\necho 2.0.1\n')
            binary.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'pinned to 1.18.31'):
                workflow.record_harness_version(cfg, str(binary), Path(tmp))
        cfg.harness.version = None  # unpinned: a rendered config only warns
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'opencode'
            for reported, warned in (('1.18.31', False), ('2.0.1', True)):
                binary.write_text(f'#!/bin/sh\necho {reported}\n')
                binary.chmod(0o755)
                with self.subTest(reported=reported), contextlib.redirect_stderr(io.StringIO()) as err:
                    record = workflow.record_harness_version(cfg, str(binary), Path(tmp))
                self.assertEqual(record['version'], reported)
                self.assertEqual('WARNING' in err.getvalue(), warned)
            cfg.harness.name = 'codex'
            binary.write_text('#!/bin/sh\necho codex-cli 0.155.1\n')
            record = workflow.record_harness_version(cfg, str(binary), Path(tmp))
            self.assertEqual(record['version'], '0.155.1')
            self.assertNotIn('opencode_version_validated', record)

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
            runs, jobs, _ = workflow.evaluation_jobs(cfg, Path(tmp) / 'evaluation job')
            convert, _ = jobs[0]
            command, cwd = jobs[1]
            self.assertEqual(convert[convert.index('--runner-output') + 1], str(runs[0]))
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
            self.assertIn(str(Path(tmp) / 'venv-vllm/bin/python'), commands[1])
            self.assertEqual(commands[1][-1], 'vllm==0.29.0')

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
