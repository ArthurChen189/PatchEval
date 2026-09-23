import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from omegaconf import OmegaConf

from patcheval.exp_agent import patch_agent_runner as runner
from scripts import run as workflow
from tests.test_hydra_workflow import ROOT, config

READY = '"type":"step_start"'


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def agent(self, code, timeout=20, startup=1.0):
        create = asyncio.create_subprocess_exec

        async def local_process(*args, **kwargs):
            return await create(sys.executable, '-u', '-c', code, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            with patch.object(runner.asyncio, 'create_subprocess_exec', side_effect=local_process):
                outcome = await runner._run_agent('case', '/workspace/repo', 'unused', Path(tmp), {}, timeout,
                                                  ready_pattern=READY, startup_timeout=startup)
            return outcome, time.monotonic() - started

    async def test_started_agent_runs_normally(self):
        outcome, _ = await self.agent('print(\'{"type":"step_start"}\', flush=True); import time; time.sleep(2)')
        self.assertFalse(outcome.startup_stalled)
        self.assertFalse(outcome.timed_out)
        self.assertEqual(outcome.exit_code, 0)

    async def test_silent_agent_is_stopped_at_the_startup_deadline(self):
        outcome, elapsed = await self.agent('import time; time.sleep(30)')
        self.assertTrue(outcome.startup_stalled)
        self.assertFalse(outcome.timed_out)
        self.assertLess(elapsed, 10)

    async def test_agent_exiting_before_ready_counts_as_stalled(self):
        outcome, _ = await self.agent('print("boot error"); raise SystemExit(0)')
        self.assertTrue(outcome.startup_stalled)

    async def test_started_agent_still_hits_the_agent_timeout(self):
        outcome, _ = await self.agent('print(\'{"type":"step_start"}\', flush=True); import time; time.sleep(30)',
                                      timeout=3)
        self.assertTrue(outcome.timed_out)
        self.assertFalse(outcome.startup_stalled)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, outcomes, retries=2):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(run_root=tmp, container_prefix='fixture', agent_command='unused',
                                      agent_timeout=60, save_trajectories=True, trajectory_path=[],
                                      ready_pattern=READY, startup_timeout=1, startup_retries=retries)
            sample = {'cve_id': 'CVE-2099-1', 'image_url': 'fixture', 'repo': 'https://example/repo'}
            queue = list(outcomes)

            async def agent(container, workdir, command, work, env, timeout, **watch):
                self.assertEqual(watch, {'ready_pattern': READY, 'startup_timeout': 1})
                stalled = queue.pop(0)
                (work / 'agent_stdout.txt').write_text('' if stalled else '{"type":"step_start"}\n')
                return runner.CommandResult([], -9 if stalled else 0, '', '', 1, False, stalled)

            async def collect(container, workdir, work):
                (work / 'llm.patch').write_text('fixture patch')
                return runner.CommandResult([], 0, '', '', 0)

            with patch.object(runner, '_run', AsyncMock(return_value=runner.CommandResult([], 0, '', '', 0))), \
                 patch.object(runner, '_detect_workdir', AsyncMock(return_value='/workspace/repo')), \
                 patch.object(runner, '_hide_workspace_payload', AsyncMock()), \
                 patch.object(runner, '_run_agent', side_effect=agent), \
                 patch.object(runner, '_collect_patch', side_effect=collect), \
                 patch.object(runner, '_remove_container', AsyncMock()) as cleanup:
                outcome = await runner._run_one(sample, 7, args, asyncio.Semaphore(1), [])
            work = Path(tmp) / '.work/00007-patcheval_CVE-2099-1'
            metadata = json.loads((Path(outcome.trajectory_path) / 'metadata.json').read_text())
            kept = sorted(p.name for p in work.glob('startup_attempt_*'))
            return outcome, metadata, kept, cleanup.await_count

    async def test_stalled_start_is_retried_in_a_fresh_container(self):
        outcome, metadata, kept, removals = await self.run_case([True, False])
        self.assertEqual((outcome.status, outcome.startup_attempts, outcome.startup_failed), ('generated', 2, False))
        self.assertEqual(kept, ['startup_attempt_1'])
        self.assertEqual(removals, 2)  # stalled container plus the final cleanup
        self.assertEqual(metadata['startup_stalls'][0]['attempt'], 1)

    async def test_exhausted_retries_mark_startup_failed(self):
        outcome, metadata, kept, _ = await self.run_case([True, True, True])
        self.assertEqual((outcome.status, outcome.startup_attempts, outcome.startup_failed), ('failed', 3, True))
        self.assertIn('did not start after 3 attempts', outcome.error)
        self.assertEqual(kept, ['startup_attempt_1', 'startup_attempt_2'])
        self.assertEqual(len(metadata['startup_stalls']), 3)


def write_run(root, rows, stdout):
    """A completed run directory with results, trajectories, and patches."""
    (root / 'patches').mkdir(parents=True)
    with (root / 'results.jsonl').open('w') as out:
        for index, (cve, generated, extra) in enumerate(rows):
            run_id = f'{index:05d}-patcheval_{cve}'
            trajectory = root / 'trajectories' / run_id
            trajectory.mkdir(parents=True)
            (trajectory / 'stdout.jsonl').write_text(stdout.get(cve, ''))
            (root / 'patches' / f'{cve}.patch').write_text('old' if generated else '')
            out.write(json.dumps({'index': index, 'cve': cve, 'patch_generated': generated,
                                  'status': 'generated' if generated else 'failed',
                                  'trajectory_path': str(trajectory), **extra}) + '\n')
    (root / 'summary.json').write_text('{}')


class RerunTests(unittest.TestCase):
    def test_startup_failures_detects_only_agents_that_never_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            timeout = {'error': 'agent failed with exit_code=-9', 'timed_out': True}
            write_run(root, [
                ('CVE-OLD-HUNG', False, timeout),
                ('CVE-OLD-TIMEOUT', False, timeout),
                ('CVE-NEW-FAILED', False, {'startup_failed': True, 'error': 'agent did not start after 3 attempts'}),
                ('CVE-NEW-STARTED', False, {'startup_failed': False, **timeout}),
                ('CVE-EMPTY-DIFF', False, {'error': 'collect patch failed: '}),
                ('CVE-OK', True, {}),
                ('CVE-INTERRUPTED', False, timeout),
            ], {'CVE-OLD-TIMEOUT': '{"type":"step_start"}\n', 'CVE-EMPTY-DIFF': ''})
            # An interrupted rerun leaves only a placeholder trajectory without stdout.
            (root / 'trajectories/00006-patcheval_CVE-INTERRUPTED/stdout.jsonl').unlink()
            self.assertEqual(workflow.startup_failures(root, READY),
                             ['CVE-OLD-HUNG', 'CVE-NEW-FAILED', 'CVE-INTERRUPTED'])
            self.assertEqual(workflow.ready_pattern('opencode'), READY)
            self.assertEqual(workflow.ready_pattern('codex'), '"type":"turn.started"')
            self.assertEqual(workflow.ready_pattern('traecli'), '')

    def test_only_cves_keep_dataset_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / 'cves.txt'
            listing.write_text('CVE-B\nCVE-D\n')
            samples = [{'cve_id': c} for c in ('CVE-A', 'CVE-B', 'CVE-C', 'CVE-D')]
            args = argparse.Namespace(only_cves_file=str(listing), limit=1)
            self.assertEqual([i for i, _ in runner._select(samples, args)], [1, 3])
            listing.write_text('CVE-Z\n')
            with self.assertRaisesRegex(ValueError, 'absent'):
                runner._select(samples, args)

    def test_rerun_into_replaces_rows_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            write_run(root, [('CVE-A', True, {}), ('CVE-B', False, {'error': 'agent failed'}),
                             ('CVE-C', True, {})], {})
            dataset = Path(tmp) / 'data.json'
            dataset.write_text(json.dumps([{'cve_id': c, 'image_url': 'img'} for c in ('CVE-A', 'CVE-B', 'CVE-C')]))
            listing = Path(tmp) / 'cves.txt'
            listing.write_text('CVE-B\n')
            args = runner.build_parser().parse_args(['--agent-command', 'unused', '--input', str(dataset),
                                                     '--rerun-into', str(root), '--only-cves-file', str(listing)])

            async def rerun(sample, index, args, sem, mounts):
                self.assertEqual((sample['cve_id'], index), ('CVE-B', 1))
                self.assertFalse((root / 'trajectories/00001-patcheval_CVE-B').exists())
                (root / 'patches/CVE-B.patch').write_text('new')
                return runner.GenerationResult(index, 'CVE-B', 'patcheval_CVE-B', 'img', '/workspace', 'c',
                                               'generated', True, 0, False, 1.0,
                                               str(root / 'patches/CVE-B.patch'), startup_attempts=2)
            with patch.object(runner, '_run_one', side_effect=rerun), patch('builtins.print'):
                self.assertEqual(asyncio.run(runner._main(args)), 0)
            rows = [json.loads(l) for l in (root / 'results.jsonl').read_text().splitlines()]
            self.assertEqual([r['cve'] for r in rows], ['CVE-A', 'CVE-B', 'CVE-C'])
            self.assertEqual((rows[1]['patch_generated'], rows[1]['startup_attempts']), (True, 2))
            self.assertEqual(json.loads((root / 'summary.json').read_text()),
                             {'total': 3, 'generated': 3, 'failed': 0})
            replaced = list(root.glob('startup_reruns/*/replaced/trajectories/00001-patcheval_CVE-B'))
            self.assertEqual(len(replaced), 1)
            log = json.loads((root / 'startup_reruns.jsonl').read_text())
            self.assertEqual((log['cves'], log['generated']), (['CVE-B'], 1))


class ResumeRerunTests(unittest.TestCase):
    def setUp(self):
        # Generation snapshots the server's /metrics; keep unit tests off the network.
        stub = patch.object(workflow, 'server_snapshot', return_value=None)
        stub.start()
        self.addCleanup(stub.stop)

    def test_resume_reruns_startup_failures_before_missing_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = config('action=generate', 'harness=opencode', 'server.host=10.0.0.9',
                              'generation.samples=2', 'label=orig')
            target = Path(tmp) / 'orig'
            target.mkdir()
            OmegaConf.save(original, target / 'resolved.yaml', resolve=True)
            opencode = target / 'harnesses/opencode/config/opencode/opencode.json'
            opencode.parent.mkdir(parents=True)
            opencode.write_text('{}')
            run0 = target / 'generation/sample_0/2026-x-orig'
            write_run(run0, [('CVE-HUNG', False, {'error': 'agent failed with exit_code=-9'}),
                             ('CVE-FINE', True, {})], {})
            calls = []

            def record(command, env, cfg, cwd=None):
                calls.append(env)
                if env.get('RERUN_INTO'):
                    with (Path(env['RERUN_INTO']) / 'startup_reruns.jsonl').open('a') as log:
                        log.write('{}\n')
                    raise subprocess.CalledProcessError(1, command)
                base = Path(env['OUTPUT_BASE'])
                (base / '2026-y-orig/patches').mkdir(parents=True)
                (base / '2026-y-orig/summary.json').write_text('{}')
            cfg = config('action=generate', 'server.host=127.0.0.1', f'generation.resume_dir={target}',
                         f'paths.runtime={tmp}')
            cfg.harness.binary = '/bin/true'
            with patch.object(workflow, 'run_command', side_effect=record), \
                 patch.object(workflow, 'record_harness_version', return_value={'version': '1.18.31'}), \
                 patch('builtins.print'), patch('sys.stderr'):
                workflow.dispatch(cfg, Path(tmp) / 'resume')
            self.assertEqual(calls[0]['RERUN_INTO'], str(run0))
            self.assertEqual(Path(calls[0]['RERUN_CVES_FILE']).read_text(), 'CVE-HUNG\n')
            self.assertEqual(calls[1]['OUTPUT_BASE'], str(target / 'generation/sample_1'))
            self.assertNotIn('RERUN_INTO', calls[1])
            log = json.loads((target / 'resumed_by.jsonl').read_text())
            self.assertEqual(log['startup_reruns'][0]['cves'], ['CVE-HUNG'])


class LiveSampleGuardTests(unittest.TestCase):
    def test_recently_active_unfinished_sample_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'sample_1'
            live = base / '2026-live-run/.work/00001-task'
            live.mkdir(parents=True)
            self.assertEqual(workflow.still_running(base), base / '2026-live-run')
            stale = time.time() - 3600
            for path in (live, live.parent, live.parent.parent):
                os.utime(path, (stale, stale))
            self.assertIsNone(workflow.still_running(base))


class RunInferTests(unittest.TestCase):
    def test_adapter_marker_and_rerun_settings_reach_the_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'bin'
            fake.mkdir()
            (fake / 'python').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            (fake / 'python').chmod(0o755)
            binary = Path(tmp) / 'opencode'
            binary.write_text('#!/bin/sh\n')
            binary.chmod(0o755)
            config_file = Path(tmp) / 'home/config/opencode/opencode.json'
            config_file.parent.mkdir(parents=True)
            config_file.write_text('{}')
            (Path(tmp) / 'home/data').mkdir()
            env = {**os.environ, 'PATH': f"{fake}:{os.environ['PATH']}", 'OPENCODE_BIN': str(binary),
                   'OPENCODE_CONFIG': str(config_file), 'OPENCODE_VERSION': '', 'STARTUP_TIMEOUT': '120',
                   'RERUN_INTO': '/runs/x', 'RERUN_CVES_FILE': '/runs/cves.txt'}
            out = subprocess.run(['bash', str(ROOT / 'patcheval/exp_agent/run_infer.sh'), 'opencode', 'label'],
                                 env=env, capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(out[out.index('--ready-pattern') + 1], READY)
            self.assertEqual(out[out.index('--startup-timeout') + 1], '120')
            self.assertEqual(out[out.index('--startup-retries') + 1], '2')
            self.assertEqual(out[out.index('--rerun-into') + 1], '/runs/x')
            self.assertEqual(out[out.index('--only-cves-file') + 1], '/runs/cves.txt')


if __name__ == '__main__':
    unittest.main()
