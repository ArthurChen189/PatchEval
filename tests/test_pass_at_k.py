import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omegaconf import OmegaConf

from scripts import run as workflow
from scripts.infer import pass_at_k as scoring
from tests.test_hydra_workflow import config


def write_sample(directory, cases, solved):
    directory.mkdir(parents=True, exist_ok=True)
    patches = directory / 'patches.jsonl'
    patches.write_text(''.join(json.dumps({'cve': cve, 'language': lang, 'fix_patch': ''}) + '\n'
                               for cve, lang in cases.items()))
    summary = directory / 'summary.json'
    summary.write_text(json.dumps({'poc_evaluation': {'successful_cves': {'Go': sorted(solved)}},
                                   'execution_errors': []}))
    return patches, summary


def make_invocation(root, samples, label='multi', configured=None):
    invocation = root / 'hydra/stamp-generate'
    invocation.mkdir(parents=True)
    OmegaConf.save(OmegaConf.create({'generation': {'samples': configured or samples}}), invocation / 'resolved.yaml')
    for index in range(samples):
        run = invocation / f'generation/sample_{index}/2026-{index}-{label}'
        (run / 'patches').mkdir(parents=True)
        (run / 'summary.json').write_text('{}')
    return invocation


class PassAtKTests(unittest.TestCase):
    def test_unbiased_estimator(self):
        self.assertEqual(scoring.pass_at_k(4, 0, 4), 0.0)
        self.assertEqual(scoring.pass_at_k(4, 1, 4), 1.0)
        self.assertAlmostEqual(scoring.pass_at_k(4, 1, 1), 0.25)
        self.assertAlmostEqual(scoring.pass_at_k(4, 2, 2), 1 - (2 / 4) * (1 / 3))
        self.assertIsNone(scoring.pass_at_k(3, 1, 4))

    def test_aggregate_across_samples(self):
        cases = {'CVE-A': 'Go', 'CVE-B': 'Go', 'CVE-C': 'Python'}
        solved = [{'CVE-A'}, {'CVE-A', 'CVE-B'}, set(), {'CVE-A'}]
        with tempfile.TemporaryDirectory() as tmp:
            files = [write_sample(Path(tmp) / f's{i}', cases, s) for i, s in enumerate(solved)]
            result = scoring.aggregate(files, Path(tmp) / 'pass_at_k.json')
        self.assertEqual((result['n_samples'], result['n_cves']), (4, 3))
        self.assertAlmostEqual(result['pass@1'], (3 / 4 + 1 / 4 + 0) / 3)
        self.assertAlmostEqual(result['pass@4'], 2 / 3)
        self.assertEqual(result['per_cve_solved_count'], {'CVE-A': 3, 'CVE-B': 1, 'CVE-C': 0})
        self.assertEqual(result['per_language']['Python']['pass@4'], 0.0)
        self.assertEqual(result['per_sample_solved'], [1, 2, 0, 1])

    def test_mismatched_case_sets_and_unknown_solves_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = write_sample(Path(tmp) / 'a', {'CVE-A': 'Go'}, set())
            second = write_sample(Path(tmp) / 'b', {'CVE-B': 'Go'}, set())
            with self.assertRaisesRegex(ValueError, 'different CVE set'):
                scoring.aggregate([first, second], Path(tmp) / 'out.json')
            bad = write_sample(Path(tmp) / 'c', {'CVE-A': 'Go'}, {'CVE-Z'})
            with self.assertRaisesRegex(ValueError, 'absent'):
                scoring.aggregate([bad], Path(tmp) / 'out.json')


class MergeTests(unittest.TestCase):
    def evaluation(self, root, name, generation, solved_by_sample, server):
        invocation = root / generation
        invocation.mkdir(parents=True, exist_ok=True)
        (invocation / 'resolved.yaml').write_text('harness:\n  name: opencode\n')
        if server:
            (invocation / 'server.json').write_text(json.dumps({'argv': server}))
        evaluation = root / name
        (evaluation).mkdir()
        (evaluation / 'resolved.yaml').write_text(f'evaluation:\n  run_dir: {invocation / "generation"}\n')
        for sample, solved in solved_by_sample.items():
            write_sample(evaluation / 'eval_inputs' / sample, {'CVE-A': 'Go', 'CVE-B': 'Go'}, set())
            write_sample(evaluation / 'evaluation_output' / sample, {'CVE-A': 'Go', 'CVE-B': 'Go'}, solved)
        return evaluation

    def test_merge_pools_selected_samples_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            new = self.evaluation(root, 'eval-new', 'gen-new', {'sample_0': {'CVE-A'}, 'sample_1': set()}, ['fast'])
            old = self.evaluation(root, 'eval-old', 'gen-old',
                                  {'sample_0': {'CVE-B'}, 'sample_1': {'CVE-A'}, 'sample_2': {'CVE-A', 'CVE-B'}}, None)
            result = scoring.merge([str(new), str(old)], root / 'merged', {str(old): ['sample_0', 'sample_1']})
            self.assertEqual(result['n_samples'], 4)
            self.assertEqual(result['per_cve_solved_count'], {'CVE-A': 2, 'CVE-B': 1})
            self.assertAlmostEqual(result['pass@4'], 1.0)
            self.assertEqual([(s['evaluation'], s['sample']) for s in result['merged_from']],
                             [(str(new), 'sample_0'), (str(new), 'sample_1'), (str(old), 'sample_0'), (str(old), 'sample_1')])
            self.assertTrue(result['mixed_serving_settings'])
            self.assertEqual(result['merged_from'][0]['invocation'], str(root / 'gen-new'))
            with self.assertRaises(FileExistsError):
                scoring.merge([str(new)], root / 'merged')


class MultiSampleDiscoveryTests(unittest.TestCase):
    def test_label_and_explicit_paths_find_every_sample_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            invocation = make_invocation(Path(tmp), 4)
            cfg = config('action=evaluate', 'label=multi')
            cfg.paths.runs = tmp
            expected = [invocation / f'generation/sample_{i}/2026-{i}-multi' for i in range(4)]
            self.assertEqual(workflow.find_runs(cfg), expected)
            for explicit in (invocation, invocation / 'generation'):
                cfg.evaluation.run_dir = str(explicit)
                self.assertEqual(workflow.find_runs(cfg), expected)
            runs, jobs, results = workflow.evaluation_jobs(cfg, Path(tmp) / 'eval')
            self.assertEqual(len(jobs), 8)
            self.assertEqual(results[3], (Path(tmp) / 'eval/eval_inputs/sample_3/patches.jsonl',
                                          Path(tmp) / 'eval/evaluation_output/sample_3/summary.json'))
            report = jobs[7][0][jobs[7][0].index('--output') + 1]
            self.assertEqual((jobs[7][1] / 'evaluation_output' / report).resolve(),
                             Path(tmp) / 'eval/evaluation_output/sample_3')

    def test_incomplete_samples_are_rejected_unless_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            invocation = make_invocation(Path(tmp), 3, configured=4)
            cfg = config('action=evaluate', f'evaluation.run_dir={invocation}')
            with self.assertRaisesRegex(ValueError, r'3 of 4 configured samples.*temp_run_script.sh resume'):
                workflow.find_runs(cfg)
            (invocation / 'generation/sample_1/2026-1-multi/summary.json').unlink()
            cfg.evaluation.allow_partial = True
            runs = workflow.find_runs(cfg)
            self.assertEqual([run.parent.name for run in runs], ['sample_0', 'sample_2'])
            _, _, results = workflow.evaluation_jobs(cfg, Path(tmp) / 'eval')
            self.assertEqual([r[1].parent.name for r in results], ['sample_0', 'sample_2'])

    def test_any_sample_path_expands_to_the_whole_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            invocation = make_invocation(Path(tmp), 4)
            expected = [invocation / f'generation/sample_{i}/2026-{i}-multi' for i in range(4)]
            for path in (expected[0], expected[2], expected[1].parent):
                cfg = config('action=evaluate', f'evaluation.run_dir={path}')
                self.assertEqual(workflow.find_runs(cfg), expected)
            (invocation / 'generation/sample_3/2026-3-multi/summary.json').unlink()
            cfg = config('action=evaluate', f'evaluation.run_dir={expected[0]}')
            with self.assertRaisesRegex(ValueError, '3 of 4'):
                workflow.find_runs(cfg)

    def test_evaluate_dispatch_writes_pass_at_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            invocation = make_invocation(Path(tmp), 2)
            cfg = config('action=evaluate', f'evaluation.run_dir={invocation}')
            output = Path(tmp) / 'eval'

            def fake(command, cwd, env, check):
                if '--process-data-path' in command:
                    sample = Path(command[command.index('--process-data-path') + 1]).parent
                    solved = {'CVE-A'} if sample.name == 'sample_0' else set()
                    write_sample(sample, {'CVE-A': 'Go', 'CVE-B': 'Go'}, set())
                    write_sample(output / 'evaluation_output' / sample.name,
                                 {'CVE-A': 'Go', 'CVE-B': 'Go'}, solved)
            with patch.object(workflow.subprocess, 'run', side_effect=fake), \
                 patch('builtins.print'):
                workflow.dispatch(cfg, output)
            result = json.loads((output / 'pass_at_k.json').read_text())
            self.assertEqual((result['pass@1'], result['pass@2']), (0.25, 0.5))
            self.assertEqual((result['configured_samples'], result['partial']), (2, False))
            self.assertEqual(OmegaConf.load(output / 'resolved.yaml').evaluation.run_dir, str(invocation / 'generation'))


def completed_runner(fail_codes):
    """Fake run_command: creates a completed run, then fails with the next exit code."""
    codes = list(fail_codes)

    def run(command, env, cfg, cwd=None):
        code = codes.pop(0)
        base = Path(env['OUTPUT_BASE'])
        if code != 'crash':
            run_dir = base / f'2026-{base.name}-{cfg.label}'
            (run_dir / 'patches').mkdir(parents=True)
            (run_dir / 'summary.json').write_text('{}')
        if code:
            raise workflow.subprocess.CalledProcessError(1 if code == 'crash' else code, command)
    return run


class GenerationSampleTests(unittest.TestCase):
    def generate(self, tmp, fail_codes, *overrides):
        cfg = config('action=generate', 'harness=opencode', 'server.host=127.0.0.1', *overrides)
        cfg.harness.binary = '/bin/true'
        with patch.object(workflow, 'run_command', side_effect=completed_runner(fail_codes)), \
             patch.object(workflow, 'record_harness_version', return_value={'version': '1.18.31'}), \
             patch('builtins.print'):
            workflow.dispatch(cfg, Path(tmp) / 'job')
        return cfg

    def test_failed_tasks_do_not_stop_later_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.generate(tmp, [1, 0, 1, 0])
            self.assertEqual(sorted(d.name for d in (Path(tmp) / 'job/generation').iterdir()),
                             [f'sample_{i}' for i in range(4)])

    def test_incomplete_sample_is_reported_after_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, r'Samples \[1\] did not complete.*resume_dir'):
                self.generate(tmp, [0, 'crash', 0, 0])
            self.assertTrue((Path(tmp) / 'job/generation/sample_3').is_dir())

    def test_resume_generates_only_missing_samples_with_original_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = config('action=generate', 'harness=opencode', 'server.host=10.0.0.9',
                              'generation.concurrency=8', 'label=orig_label')
            target = Path(tmp) / 'orig'
            target.mkdir()
            OmegaConf.save(original, target / 'resolved.yaml', resolve=True)
            (target / 'harness-version.json').write_text(json.dumps({'version': '1.18.31'}))
            opencode = target / 'harnesses/opencode/config/opencode/opencode.json'
            opencode.parent.mkdir(parents=True)
            opencode.write_text('{}')
            done = target / 'generation/sample_0/2026-x-orig_label'
            (done / 'patches').mkdir(parents=True)
            (done / 'summary.json').write_text('{}')
            calls = []
            fake = completed_runner([1, 0, 0])

            def record(command, env, cfg, cwd=None):
                calls.append(env)
                fake(command, env, cfg, cwd)
            runtime = Path(tmp) / 'runtime'
            runtime.mkdir()
            server = {'argv': ['serve', 'Qwen/Qwen3.8-27B', '--max-num-seqs', '8'], 'vllm': '0.29.0'}
            (runtime / 'current-server.json').write_text(json.dumps(server))
            (target / 'server.json').write_text(json.dumps(server))
            cfg = config('action=generate', 'server.host=127.0.0.1', f'generation.resume_dir={target}',
                         f'paths.runtime={runtime}')
            cfg.harness.binary = '/bin/true'
            with patch.object(workflow, 'run_command', side_effect=record), \
                 patch.object(workflow, 'record_harness_version', return_value={'version': '1.18.31'}), \
                 patch('builtins.print'):
                workflow.dispatch(cfg, Path(tmp) / 'resume')
            self.assertEqual([env['OUTPUT_BASE'] for env in calls],
                             [str(target / f'generation/sample_{i}') for i in (1, 2, 3)])
            self.assertEqual(calls[0]['OPENCODE_CONFIG'], str(opencode))
            self.assertEqual((cfg.harness.name, cfg.label, cfg.server.host, cfg.generation.concurrency),
                             ('opencode', 'orig_label', '10.0.0.9', 8))
            log = json.loads((target / 'resumed_by.jsonl').read_text())
            self.assertEqual((log['samples'], log['incomplete']), ([1, 2, 3], []))
            evaluate = config('action=evaluate', f'evaluation.run_dir={done}')
            self.assertEqual(len(workflow.find_runs(evaluate)), 4)
            self.assertEqual(json.loads((Path(tmp) / 'resume/server.json').read_text()), server)

            (target / 'generation/sample_3').rename(target / 'generation/sample_3.bak')
            with patch.object(workflow, 'run_command') as never, \
                 patch.object(workflow, 'record_harness_version', return_value={'version': '1.19.0'}), \
                 patch('builtins.print'):
                with self.assertRaisesRegex(ValueError, 'Harness version changed'):
                    workflow.dispatch(cfg, Path(tmp) / 'resume2')
                never.assert_not_called()
            changed = {**server, 'argv': server['argv'][:-1] + ['1']}
            (runtime / 'current-server.json').write_text(json.dumps(changed))
            with patch.object(workflow, 'run_command') as never, \
                 patch.object(workflow, 'record_harness_version', return_value={'version': '1.18.31'}), \
                 patch('builtins.print'):
                with self.assertRaisesRegex(ValueError, 'serving configuration differs'):
                    workflow.dispatch(cfg, Path(tmp) / 'resume3')
                never.assert_not_called()


if __name__ == '__main__':
    unittest.main()
