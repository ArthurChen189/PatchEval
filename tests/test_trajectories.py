import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from patcheval.exp_agent import patch_agent_runner as runner


def result(code=0, stderr=''):
    return runner.CommandResult([], code, '', stderr, 0)


class AgentStreamTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, timeout, code):
        create = asyncio.create_subprocess_exec

        async def local_process(*args, **kwargs):
            return await create(sys.executable, '-u', '-c', code, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(runner.asyncio, 'create_subprocess_exec', side_effect=local_process):
                outcome = await runner._run_agent('case', '/workspace/repo', 'unused', directory, {}, timeout)
            return outcome, (directory / 'agent_stdout.txt').read_bytes(), (directory / 'agent_stderr.txt').read_bytes()

    async def test_complete_streams_are_not_truncated_in_files(self):
        payload = json.dumps({'tool_output': 'x' * 100000}) + '\n'
        outcome, stdout, stderr = await self.capture(5, f'import sys; sys.stdout.write({payload!r}); sys.stderr.write("diagnostic")')
        self.assertEqual(stdout, payload.encode())
        self.assertEqual(stderr, b'diagnostic')
        self.assertEqual(outcome.exit_code, 0)
        self.assertLessEqual(len(outcome.stdout), 8192)

    async def test_timeout_retains_partial_stdout_and_stderr(self):
        outcome, stdout, stderr = await self.capture(1, 'import sys,time; print("partial event",flush=True); print("partial error",file=sys.stderr,flush=True); time.sleep(30)')
        self.assertTrue(outcome.timed_out)
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(stdout, b'partial event\n')
        self.assertEqual(stderr, b'partial error\n')

    async def test_nonzero_exit_retains_events(self):
        outcome, stdout, _ = await self.capture(5, 'import sys; print("failed event"); sys.exit(7)')
        self.assertEqual(outcome.exit_code, 7)
        self.assertEqual(stdout, b'failed event\n')


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_capture_stops_writers_and_reports_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, destination = Path(tmp) / 'work', Path(tmp) / 'trajectory'
            work.mkdir()
            (work / 'prompt.txt').write_text('task prompt')
            (work / 'agent_stdout.txt').write_bytes(b'{"tool":"read"}\n')
            (work / 'agent_stderr.txt').write_text('diagnostic')
            execute = AsyncMock(side_effect=[result(), result(), result(1, 'missing optional file')])
            with patch.object(runner, '_run', execute):
                capture = await runner._archive_trajectory('case', work, destination,
                                                           [('sessions', '/sessions'), ('db', '/missing')])
            self.assertEqual((destination / 'stdout.jsonl').read_bytes(), b'{"tool":"read"}\n')
            self.assertEqual((destination / 'prompt.txt').read_text(), 'task prompt')
            self.assertEqual(execute.call_args_list[0].args[0][:2], ['docker', 'stop'])
            self.assertTrue(capture['native']['sessions']['saved'])
            self.assertFalse(capture['native']['db']['saved'])

    async def run_case(self, save, timed_out=False):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(run_root=tmp, container_prefix='fixture',
                                      agent_command='unused', agent_timeout=1,
                                      save_trajectories=save, trajectory_path=[])
            sample = {'cve_id': 'CVE-2099-1', 'image_url': 'fixture', 'repo': 'https://example/repo'}

            async def agent(container, workdir, command, work, env, timeout):
                (work / 'agent_stdout.txt').write_text('{"event":"partial"}\n')
                (work / 'agent_stderr.txt').write_text('diagnostic')
                return runner.CommandResult([], -9 if timed_out else 0, '', '', 1, timed_out)

            async def collect(container, workdir, work):
                (work / 'llm.patch').write_text('fixture patch')
                return result()

            with patch.object(runner, '_run', AsyncMock(return_value=result())), \
                 patch.object(runner, '_detect_workdir', AsyncMock(return_value='/workspace/repo')), \
                 patch.object(runner, '_hide_workspace_payload', AsyncMock()), \
                 patch.object(runner, '_run_agent', side_effect=agent), \
                 patch.object(runner, '_collect_patch', side_effect=collect), \
                 patch.object(runner, '_remove_container', AsyncMock()) as cleanup:
                outcome = await runner._run_one(sample, 0, args, asyncio.Semaphore(1), [])
            cleanup.assert_awaited_once()
            if save:
                folder = Path(outcome.trajectory_path)
                metadata = json.loads((folder / 'metadata.json').read_text())
                self.assertEqual(metadata['timed_out'], timed_out)
                self.assertEqual(metadata['status'], outcome.status)
                self.assertEqual(metadata['agent_duration_s'], 1)
                self.assertTrue((folder / 'prompt.txt').is_file())
                self.assertEqual((folder / 'stdout.jsonl').read_text(), '{"event":"partial"}\n')
            else:
                self.assertIsNone(outcome.trajectory_path)
                self.assertFalse((Path(tmp) / 'trajectories').exists())
            return outcome

    async def test_success_archive(self):
        self.assertTrue((await self.run_case(True)).patch_generated)

    async def test_timeout_archive(self):
        self.assertTrue((await self.run_case(True, timed_out=True)).timed_out)

    async def test_disabled_retains_original_work_logs_only(self):
        self.assertTrue((await self.run_case(False)).patch_generated)


class ParserTests(unittest.TestCase):
    def test_flag_defaults_off_for_direct_runner_and_accepts_native_specs(self):
        parser = runner.build_parser()
        self.assertFalse(parser.parse_args(['--agent-command', 'command']).save_trajectories)
        args = parser.parse_args(['--agent-command', 'command', '--save-trajectories',
                                  '--trajectory-path', 'sessions=/tmp/sessions'])
        self.assertTrue(args.save_trajectories)
        self.assertEqual(args.trajectory_path, [('sessions', '/tmp/sessions')])
        with self.assertRaises(argparse.ArgumentTypeError):
            runner._trajectory_spec('../escape=/tmp/sessions')
