import contextlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from scripts import run as workflow
from scripts.infer import token_usage as usage
from tests.test_hydra_workflow import config
from tests.test_pass_at_k import write_sample


def codex_trajectory(root, responses, finished=True):
    """responses: list of (input, cached, output, reasoning) per model response."""
    root.mkdir(parents=True)
    session = root / 'native/codex_sessions/2026/09/23/rollout.jsonl'
    session.parent.mkdir(parents=True)
    events, total = [], {'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0,
                         'reasoning_output_tokens': 0}
    for i, (inp, cached, out, reasoning) in enumerate(responses):
        one = {'input_tokens': inp, 'cached_input_tokens': cached, 'cache_write_input_tokens': 0,
               'output_tokens': out, 'reasoning_output_tokens': reasoning}
        events.append({'type': 'token_usage_record', 'ordinal': 2 * i,
                       'payload': {'response_id': f'resp_{i}', 'usage': one}})
        # A duplicate record for the same response must not be counted twice.
        events.append({'type': 'token_usage_record', 'ordinal': 2 * i + 1,
                       'payload': {'response_id': f'resp_{i}', 'usage': one}})
        total = {k: total[k] + one[k] for k in total}
        events.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': dict(total), 'last_token_usage': one}}})
    session.write_text(''.join(json.dumps(e) + '\n' for e in events))
    stdout = [{'type': 'thread.started'}, {'type': 'turn.started'}]
    if finished:
        stdout.append({'type': 'turn.completed', 'usage': total})
    (root / 'stdout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in stdout))
    return root


def opencode_trajectory(root, messages, stdout_steps, database=True, wal=False):
    """messages: (agent, input, output, reasoning, cache_read); stdout_steps: primary steps in stdout."""
    native = root / 'native'
    native.mkdir(parents=True)
    step = lambda m, reason='stop': {'type': 'step_finish', 'part': {'reason': reason, 'tokens': {
        'input': m[1], 'output': m[2], 'reasoning': m[3], 'cache': {'read': m[4], 'write': 0}}}}
    (root / 'stdout.jsonl').write_text(''.join(json.dumps(step(m)) + '\n' for m in stdout_steps))
    if not database:
        return root
    path = native / 'opencode.db'
    connection = sqlite3.connect(path)
    if wal:
        connection.execute('pragma journal_mode=wal')
        connection.execute('pragma wal_autocheckpoint=0')
    connection.execute('create table message (id text primary key, session_id text, data text)')
    rows = [{'role': 'user'}] + [{'role': 'assistant', 'agent': a, 'finish': 'stop', 'tokens': {
        'input': i, 'output': o, 'reasoning': r, 'cache': {'read': c, 'write': 0}}} for a, i, o, r, c in messages]
    rows.append({'role': 'assistant', 'agent': 'build', 'tokens': {  # in-flight placeholder
        'input': 0, 'output': 0, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}})
    connection.executemany('insert into message values (?, ?, ?)',
                           [(f'm{i}', 's', json.dumps(row)) for i, row in enumerate(rows)])
    connection.commit()
    if wal:
        # Archive the files while the WAL still holds the rows, as a live copy would.
        snapshot = root / 'snapshot'
        snapshot.mkdir()
        for name in ('opencode.db', 'opencode.db-wal', 'opencode.db-shm'):
            if (native / name).exists():
                shutil.copy2(native / name, snapshot / name)
        connection.close()
        shutil.rmtree(native)
        snapshot.rename(native)
    else:
        connection.close()
    return root


class TaskUsageTests(unittest.TestCase):
    def test_codex_counts_each_response_once_and_survives_timeouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = usage.codex_usage(codex_trajectory(Path(tmp) / 'done', [(100, 0, 10, 4), (150, 90, 20, 5)]))
            killed = usage.codex_usage(codex_trajectory(Path(tmp) / 'killed', [(100, 0, 10, 4)], finished=False))
        self.assertEqual((done['requests'], done['input_tokens'], done['cached_input_tokens']), (2, 250, 90))
        self.assertEqual((done['uncached_input_tokens'], done['output_tokens'], done['reasoning_tokens']),
                         (160, 30, 9))
        self.assertEqual(done['max_request_input_tokens'], 150)
        self.assertEqual(done['consistency'], {'token_count_total_matches': True})
        self.assertTrue(done['final_record'])
        # No turn.completed in stdout, but the native records still give the usage.
        self.assertEqual((killed['input_tokens'], killed['source'], killed['final_record']), (100, 'codex_native', False))
        self.assertNotIn('consistency', killed)

    def test_opencode_normalizes_reasoning_counts_subagents_and_reads_wal(self):
        messages = [('build', 100, 5, 7, 0), ('explore', 40, 2, 3, 0), ('build', 120, 6, 1, 0)]
        primary = [m for m in messages if m[0] == 'build']
        with tempfile.TemporaryDirectory() as tmp:
            record = usage.opencode_usage(opencode_trajectory(Path(tmp) / 'a', messages, primary, wal=True))
            stdout_only = usage.opencode_usage(opencode_trajectory(Path(tmp) / 'b', [], primary, database=False))
            reported = usage.opencode_usage(opencode_trajectory(Path(tmp) / 'c', messages, primary),
                                            cache_reported=True)
            cached = usage.opencode_usage(opencode_trajectory(Path(tmp) / 'd', [('build', 50, 1, 0, 30)],
                                                              [('build', 50, 1, 0, 30)]))
        self.assertEqual(record['source'], 'opencode_db')
        self.assertEqual((record['requests'], record['input_tokens']), (3, 260))  # placeholder skipped
        self.assertEqual((record['output_tokens'], record['reasoning_tokens']), (24, 11))  # output + reasoning
        self.assertIsNone(record['cached_input_tokens'])  # not reported by the server: unknown, not 0
        self.assertEqual(record['by_agent']['explore']['requests'], 1)
        self.assertEqual(record['subagent_requests'], 1)
        self.assertEqual(record['consistency'], {'stdout_matches_primary_agents': True})
        self.assertEqual((stdout_only['source'], stdout_only['input_tokens']), ('opencode_stdout', 220))
        self.assertEqual(reported['cached_input_tokens'], 0)
        # OpenCode's input excludes cache reads; they are added back to the prompt size.
        self.assertEqual((cached['input_tokens'], cached['cached_input_tokens'], cached['uncached_input_tokens']),
                         (80, 30, 50))


class RunAndEvaluationUsageTests(unittest.TestCase):
    def make_run(self, root, harness='codex'):
        invocation = root / 'group/stamp-generate'
        invocation.mkdir(parents=True)
        (invocation / 'resolved.yaml').write_text(f'harness:\n  name: {harness}\ngeneration:\n  samples: 1\n')
        run = invocation / 'generation/sample_0/2026-run'
        rows = []
        for i, (cve, responses, timed_out) in enumerate([('CVE-A', [(100, 60, 10, 2)], False),
                                                         ('CVE-B', [(300, 0, 30, 6), (50, 0, 5, 1)], True),
                                                         ('CVE-C', [], False)]):
            trajectory = run / f'trajectories/{i:05d}-{cve}'
            if responses:
                codex_trajectory(trajectory, responses, finished=not timed_out)
            rows.append({'cve': cve, 'status': 'failed' if timed_out else 'generated', 'timed_out': timed_out,
                         'agent_exit_code': -9 if timed_out else 0, 'duration_s': 5.0,
                         'trajectory_path': str(trajectory)})
        (run / 'patches').mkdir(parents=True)
        (run / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        (run / 'summary.json').write_text('{}')
        dataset = root / 'dataset.json'
        dataset.write_text(json.dumps([{'cve_id': 'CVE-A', 'programming_language': 'Go'},
                                       {'cve_id': 'CVE-B', 'programming_language': 'Go'},
                                       {'cve_id': 'CVE-C', 'programming_language': 'Python'}]))
        return invocation, run, dataset

    def test_run_summary_and_evaluation_join_solved_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invocation, run, dataset = self.make_run(root)
            rows, summary = usage.run_usage(run, dataset=dataset)
            self.assertTrue((run / 'token_usage.jsonl').is_file())
            self.assertEqual(summary['harness'], 'codex')
            self.assertEqual((summary['tasks'], summary['tasks_with_usage'], summary['tasks_incomplete']), (3, 2, 1))
            self.assertEqual((summary['input_tokens'], summary['cached_input_tokens'], summary['output_tokens']),
                             (450, 60, 45))
            self.assertEqual(summary['per_language']['Go']['input_tokens'], 450)
            self.assertIsNone(rows[2]['usage'])
            evaluation = root / 'group/stamp-evaluate'
            evaluation.mkdir()
            (evaluation / 'resolved.yaml').write_text(f'evaluation:\n  run_dir: {invocation / "generation"}\n')
            cases = {'CVE-A': 'Go', 'CVE-B': 'Go', 'CVE-C': 'Python'}
            write_sample(evaluation / 'eval_inputs/sample_0', cases, set())
            write_sample(evaluation / 'evaluation_output/sample_0', cases, {'CVE-A'})
            (invocation / 'generation/sample_0/server_metrics.json').write_text('{"counters": {}}')
            report = usage.evaluation_usage([evaluation], evaluation, dataset=dataset)
            self.assertTrue((evaluation / 'token_usage.json').is_file())
        self.assertEqual(report['solved_runs'], 1)
        self.assertEqual(report['tokens_per_solved_run']['total_tokens'], 495)
        self.assertEqual(report['solved_vs_unsolved']['True']['input_tokens'], 100)
        self.assertEqual(report['per_sample'][0]['server_metrics'], {'counters': {}})
        self.assertEqual(report['per_cve']['CVE-B'][0]['requests'], 2)

    def test_unknown_cache_split_propagates_to_totals(self):
        rows = [{'complete': True, 'usage': {'requests': 1, 'input_tokens': 10, 'cached_input_tokens': None,
                                             'uncached_input_tokens': None, 'output_tokens': 2, 'reasoning_tokens': 1}},
                {'complete': True, 'usage': {'requests': 1, 'input_tokens': 5, 'cached_input_tokens': 3,
                                             'uncached_input_tokens': 2, 'output_tokens': 1, 'reasoning_tokens': 0}}]
        block = usage.totals(rows)
        self.assertIsNone(block['cached_input_tokens'])
        self.assertEqual((block['input_tokens'], block['cache_unknown_tasks']), (15, 1))
        prices = usage.parse_prices('input=1,cached=0.1,output=4')
        self.assertEqual(usage.cost(block, prices), {'usd': (15 * 1 + 3 * 4) / 1e6, 'upper_bound_cache_unknown': True})
        known = usage.totals(rows[1:])
        self.assertAlmostEqual(usage.cost(known, prices)['usd'], (2 * 1 + 3 * 0.1 + 1 * 4) / 1e6)
        with self.assertRaises(ValueError):
            usage.parse_prices('input=1')

    def test_compare_writes_table_and_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invocation, run, dataset = self.make_run(root)
            evaluation = root / 'group/stamp-evaluate'
            evaluation.mkdir()
            (evaluation / 'resolved.yaml').write_text(f'evaluation:\n  run_dir: {invocation / "generation"}\n')
            cases = {'CVE-A': 'Go', 'CVE-B': 'Go', 'CVE-C': 'Python'}
            write_sample(evaluation / 'eval_inputs/sample_0', cases, set())
            write_sample(evaluation / 'evaluation_output/sample_0', cases, {'CVE-A'})
            with patch.object(usage, 'DATASET', dataset):
                report = usage.evaluation_usage([evaluation], root / 'a', dataset=dataset)
            (root / 'b').mkdir()
            (root / 'b/token_usage.json').write_text(json.dumps(report))
            with contextlib.redirect_stdout(io.StringIO()):
                usage.main(['--compare', str(root / 'a/token_usage.json'), str(root / 'b/token_usage.json'),
                            '--labels', 'codex,opencode', '--prices', 'input=1,cached=0.1,output=4',
                            '--out', str(root / 'cmp')])
            table = (root / 'cmp/token_comparison.md').read_text()
            result = json.loads((root / 'cmp/token_comparison.json').read_text())
            svg = ElementTree.parse(root / 'cmp/token_usage.svg').getroot()
        self.assertIn('| codex |', table)
        self.assertEqual(result['harnesses'][0]['input_tokens'], 450)
        self.assertAlmostEqual(result['harnesses'][0]['cost']['usd'], (390 * 1 + 60 * 0.1 + 45 * 4) / 1e6)
        segments = [e for e in svg.iter('{http://www.w3.org/2000/svg}rect') if e.get('class') == 'segment']
        self.assertEqual(len(segments), 2 * 2 * 4)  # panels x harnesses x (uncached, cached, output, reasoning)


class ServerMetricsTests(unittest.TestCase):
    METRICS = '''# HELP vllm:prompt_tokens_total Number of prefill tokens processed.
vllm:prompt_tokens_total{engine="0",model_name="m"} 100.0
vllm:prompt_tokens_total{engine="1",model_name="m"} 50.0
vllm:prompt_tokens_cached_total{engine="0",model_name="m"} 1e+01
vllm:generation_tokens_total{engine="0",model_name="m"} 7.0
vllm:request_success_total{engine="0",finished_reason="stop",model_name="m"} 3.0
vllm:request_success_total{engine="0",finished_reason="length",model_name="m"} 1.0
vllm:prompt_tokens_by_source_total{engine="0",source="x"} 999.0
'''

    def test_counters_are_summed_over_engines_and_differenced(self):
        before = usage.parse_metrics(self.METRICS)
        self.assertEqual(before, {'prompt_tokens_total': 150.0, 'prompt_tokens_cached_total': 10.0,
                                  'generation_tokens_total': 7.0, 'request_success_total': 4.0})
        after = usage.parse_metrics(self.METRICS.replace('} 100.0', '} 400.0'))
        delta = usage.metrics_delta(before, after, 3600, 8)
        self.assertEqual(delta['counters']['prompt_tokens_total'], 300.0)
        self.assertEqual(delta['gpu_hours'], 8)

    def test_generation_records_server_metrics_and_run_usage(self):
        cfg = config('action=generate', 'harness=codex', 'server.host=127.0.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'sample_0'
            run = base / 'run'
            (run / 'patches').mkdir(parents=True)
            (run / 'results.jsonl').write_text('')
            snapshots = iter([{'prompt_tokens_total': 10.0}, {'prompt_tokens_total': 25.0}])
            with patch.object(workflow, 'server_snapshot', side_effect=lambda cfg: next(snapshots)), \
                    contextlib.redirect_stdout(io.StringIO()):
                before = workflow.server_snapshot(cfg)
                workflow.record_usage(cfg, base, [run], before, started=0)
            record = json.loads((base / 'server_metrics.json').read_text())
            self.assertTrue((run / 'token_usage_summary.json').is_file())
        self.assertEqual(record['counters'], {'prompt_tokens_total': 15.0})
        self.assertEqual(record['gpus'], cfg.server.data_parallel * cfg.server.tensor_parallel)

    def test_serving_reports_cached_prompt_tokens_for_chat_completions(self):
        cfg = config('server.host=127.0.0.1')
        self.assertIn('--enable-prompt-tokens-details', workflow.serve_command(cfg))
        cfg.server.prompt_tokens_details = False
        self.assertIn('--no-enable-prompt-tokens-details', workflow.serve_command(cfg))
        with self.assertRaisesRegex(ValueError, 'prompt_tokens_details'):
            workflow.validate(config('server.host=127.0.0.1', 'server.extra_args=[--enable-prompt-tokens-details]'))


if __name__ == '__main__':
    unittest.main()
