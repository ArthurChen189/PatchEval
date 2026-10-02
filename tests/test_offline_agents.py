import asyncio
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from omegaconf import OmegaConf

from patcheval.exp_agent import patch_agent_runner as runner
from scripts import run as workflow
from scripts.infer import contamination_audit as audit
from scripts.infer import pass_at_k as scoring
from scripts.infer.configure_harnesses import configure
from tests.test_hydra_workflow import ROOT, config


def completed(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr='')


class RunnerNetworkTests(unittest.IsolatedAsyncioTestCase):
    def test_containers_join_the_requested_network(self):
        args = runner._docker_run_args('c', 'img', Path('/tmp'), [], 'patcheval-offline')
        self.assertEqual(args[args.index('--network') + 1], 'patcheval-offline')
        self.assertLess(args.index('--network'), args.index('img'))
        for online in (None, runner.ONLINE_NETWORK):
            self.assertNotIn('--network', runner._docker_run_args('c', 'img', Path('/tmp'), [], online))

    def test_parser_defaults_to_the_offline_network(self):
        args = runner.build_parser().parse_args(['--agent-command', 'true'])
        self.assertEqual(args.network, 'patcheval-offline')

    async def test_network_must_be_internal(self):
        ok = runner.CommandResult([], 0, 'true\n', '', 0.1)
        with patch.object(runner, '_run', AsyncMock(return_value=ok)) as run:
            info = await runner._check_network('patcheval-offline')
        self.assertEqual(info, {'network': 'patcheval-offline', 'network_internal': True})
        self.assertIn('inspect', run.call_args.args[0])
        for result, message in ((runner.CommandResult([], 0, 'false\n', '', 0.1), 'not internal'),
                                (runner.CommandResult([], 1, '', 'No such network', 0.1), 'not found')):
            with patch.object(runner, '_run', AsyncMock(return_value=result)):
                with self.assertRaisesRegex(ValueError, message):
                    await runner._check_network('bridgey')
        with patch.object(runner, '_run', AsyncMock(side_effect=AssertionError('no docker call'))), \
             patch('builtins.print'):
            self.assertFalse((await runner._check_network(runner.ONLINE_NETWORK))['network_internal'])

    def test_bash_runner_and_opencode_adapter_are_offline(self):
        infer = (ROOT / 'patcheval/exp_agent/run_infer.sh').read_text()
        self.assertIn('--network "${AGENT_NETWORK:-patcheval-offline}"', infer)
        adapter = (ROOT / 'patcheval/exp_agent/agents/opencode.sh').read_text()
        for flag in ('MODELS_FETCH', 'AUTOUPDATE', 'DEFAULT_PLUGINS', 'LSP_DOWNLOAD', 'SHARE'):
            self.assertIn(f'OPENCODE_DISABLE_{flag}=1', adapter)
        self.assertIn('unset OPENCODE_ENABLE_EXA', adapter)


class WorkflowNetworkTests(unittest.TestCase):
    def test_offline_network_is_created_internal_and_its_gateway_is_used(self):
        calls = []

        def docker(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ['docker', 'network', 'inspect']:
                created = any(call[:3] == ['docker', 'network', 'create'] for call in calls)
                return completed('172.18.0.1 true\n') if created else completed(returncode=1)
            return completed()
        with patch.object(workflow.subprocess, 'run', side_effect=docker), patch('builtins.print'):
            self.assertEqual(workflow.ensure_offline_network('patcheval-offline'), '172.18.0.1')
        self.assertIn(['docker', 'network', 'create', '--internal', '--driver', 'bridge', 'patcheval-offline'], calls)
        with patch.object(workflow.subprocess, 'run', return_value=completed('172.17.0.1 false\n')):
            with self.assertRaisesRegex(ValueError, 'not internal'):
                workflow.ensure_offline_network('bridge')
        with patch.object(workflow.subprocess, 'run', return_value=completed(returncode=1)):
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                workflow.ensure_offline_network('patcheval-offline', create=False)

    def test_server_binds_the_agent_network_gateway(self):
        cfg = config('action=serve')
        self.assertIsNone(cfg.server.host)
        with patch.object(workflow, 'network_info', return_value=('172.18.0.1', True)) as info:
            self.assertEqual(workflow.endpoint(cfg), 'http://172.18.0.1:30000/v1')
        info.assert_called_with('patcheval-offline')
        online = config('action=serve', 'generation.network=null')
        with patch.object(workflow, 'network_info', return_value=('172.17.0.1', False)) as info:
            self.assertEqual(workflow.bind_host(online), '172.17.0.1')
        info.assert_called_with('bridge')

    def preflight(self, stdout):
        cfg = config('action=generate', 'server.host=172.18.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(workflow.subprocess, 'run', return_value=completed(stdout)) as run, \
                 patch('builtins.print'):
                try:
                    workflow.network_preflight(cfg, Path(tmp))
                finally:
                    record = json.loads((Path(tmp) / 'network-check.json').read_text())
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--network') + 1], 'patcheval-offline')
        self.assertIn('/dev/tcp/172.18.0.1/30000', argv[-1])
        return record

    def test_preflight_requires_model_reachable_and_internet_blocked(self):
        offline = 'model=reachable\ninternet_ip=blocked\ndns=fails\ninternet_name=blocked\n'
        record = self.preflight(offline)
        self.assertEqual(record['results']['internet_ip'], 'blocked')
        with self.assertRaisesRegex(ValueError, 'reaches the internet'):
            self.preflight(offline.replace('internet_ip=blocked', 'internet_ip=open'))
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            self.preflight(offline.replace('model=reachable', 'model=unreachable'))

    def test_generation_passes_the_network_to_the_runner(self):
        cfg = config('action=generate', 'server.host=127.0.0.1', 'dry_run=true')
        cfg.harness.binary = '/bin/true'
        with tempfile.TemporaryDirectory() as tmp:
            _, env = workflow.generation_job(cfg, Path(tmp))
            self.assertEqual(env['AGENT_NETWORK'], 'patcheval-offline')
            cfg.generation.network = None
            self.assertEqual(workflow.generation_job(cfg, Path(tmp))[1]['AGENT_NETWORK'], 'default')

    def test_resume_of_an_online_invocation_stays_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'old'
            target.mkdir()
            stored = OmegaConf.to_container(config('action=generate', 'server.host=172.17.0.1'), resolve=True)
            del stored['generation']['network']
            OmegaConf.save(OmegaConf.create(stored), target / 'resolved.yaml')
            cfg = config('action=generate', f'generation.resume_dir={target}')
            with patch('builtins.print'), patch('sys.stderr'):
                workflow.prepare_resume(cfg, Path(tmp) / 'resume')
            self.assertIsNone(cfg.generation.network)


class HarnessConfigTests(unittest.TestCase):
    def test_web_tools_are_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = configure(Path(tmp), 'http://172.18.0.1:30000/v1', 'model', 262144)
            opencode = json.loads((home / 'opencode/config/opencode/opencode.json').read_text())
            self.assertEqual(opencode['permission'],
                             {'*': 'allow', 'webfetch': 'deny', 'websearch': 'deny', 'codesearch': 'deny'})
            self.assertRegex((home / 'codex/local.config.toml').read_text(), r'(?m)^web_search = "disabled"$')


class RestrictTests(unittest.TestCase):
    def test_excluded_cves_leave_the_estimate_and_error_bars(self):
        cases = {'CVE-A': 'Go', 'CVE-B': 'Go', 'CVE-C': 'Python'}
        solved = [{'CVE-A', 'CVE-B'}, {'CVE-A'}]
        report = scoring.summarize([(cases, s, ()) for s in solved])
        report['per_sample_execution_errors'] = [0, 1]
        restricted = scoring.restrict(report, {'CVE-B', 'CVE-Z'})
        expected = scoring.summarize([({'CVE-A': 'Go', 'CVE-C': 'Python'}, s - {'CVE-B'}, ()) for s in solved])
        self.assertEqual(restricted['n_cves'], 2)
        self.assertEqual(restricted['pass@1'], expected['pass@1'])
        self.assertEqual(restricted['uncertainty'], expected['uncertainty'])
        self.assertEqual(restricted['restricted']['excluded_cves'], ['CVE-B'])
        self.assertEqual(restricted['per_sample_execution_errors'], [0, 1])
        with self.assertRaisesRegex(ValueError, 'Every CVE'):
            scoring.restrict(report, set(cases))

    def test_cve_lists_accept_json_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (('a.json', '["CVE-1", "CVE-2"]'), ('b.json', '{"excluded_cves": ["CVE-1", "CVE-2"]}'),
                               ('c.txt', '# flagged\nCVE-1\nCVE-2\n')):
                (Path(tmp) / name).write_text(text)
                self.assertEqual(scoring.load_cve_list(Path(tmp) / name), {'CVE-1', 'CVE-2'})


TARGET = audit.target_of({'repo': 'https://github.com/gogs/gogs'})
NODE = audit.target_of({'repo': 'https://github.com/isaacs/node-tar'})


def shell(command, output='', exit_code=0):
    return {'source': 'shell', 'command': command, 'output': output, 'exit_code': exit_code}


class ContaminationRuleTests(unittest.TestCase):
    def rules(self, event, target=TARGET):
        return audit.classify(event, target)[0]

    def test_target_upstream_advisories_and_packages_are_flagged(self):
        self.assertEqual(self.rules(shell('curl -s https://patch-diff.githubusercontent.com/raw/gogs/gogs/pull/7359.diff',
                                          'diff --git a/x b/x')), ['a:target-upstream'])
        self.assertEqual(self.rules(shell('curl -s https://nvd.nist.gov/vuln/detail/CVE-2022-2024', '<html>')),
                         ['b:advisory'])
        self.assertEqual(self.rules({'source': 'webfetch', 'url': 'https://huntr.dev/bounties/1/',
                                     'status': 'completed', 'output': 'page'}), ['b:advisory'])
        self.assertEqual(self.rules(shell('npm install tar@4.4.8', 'added 1 package in 2s'), NODE),
                         ['c:target-package-install'])
        self.assertEqual(self.rules(shell('go get github.com/gogs/gogs@v0.12.11', 'go: downloading github.com/gogs/gogs')),
                         ['c:target-package-install'])
        self.assertEqual(self.rules(shell('git fetch origin', 'From https://github.com/gogs/gogs')), ['a:git-remote'])
        self.assertEqual(self.rules(shell('curl -s https://cdn.jsdelivr.net/npm/tar@4.4.8/lib/parse.js', 'code'), NODE),
                         ['a:target-package-file'])

    def test_failures_mentions_and_other_projects_are_not_flagged(self):
        clean = [
            shell('curl -s https://raw.githubusercontent.com/gogs/gogs/main/x.go', '404: Not Found'),
            shell('curl https://github.com/gogs/gogs', 'curl: (6) Could not resolve host: github.com', 6),
            shell('grep -rn "https://github.com/gogs/gogs" .', 'README.md: https://github.com/gogs/gogs'),
            shell('curl -s https://raw.githubusercontent.com/other/lib/main/x.go', 'package lib'),
            shell('pip install requests', 'Successfully installed requests-2.0'),
            shell('git fetch origin', 'fatal: unable to access', 128),
            shell('git clone -q /workspace/gogs /tmp/verify && git apply --check /workspace/fix.patch', 'OK'),
            shell('git ls-remote git://127.0.0.1:9418/repo', 'f488375\tHEAD'),
            shell('grep -rn "git pull" .', './update.py: git pull'),
            {'source': 'webfetch', 'url': 'https://github.com/gogs/gogs/issues/1', 'status': 'error', 'output': ''},
        ]
        for event in clean:
            self.assertEqual(self.rules(event), [], event)
        # Dependency installs count as network use, not contamination.
        self.assertEqual(audit.classify(shell('pip install requests', 'Downloading requests-2.0.whl'), TARGET),
                         ([], True))
        requests = audit.target_of({'repo': 'https://github.com/psf/requests'})
        only_deps = ('Requirement already satisfied: requests in /workspace/requests\n'
                     'Downloading chardet-7.6.0.whl (1.5 MB)\nSuccessfully installed chardet-7.6.0\n')
        self.assertEqual(self.rules(shell('pip install chardet requests', only_deps), requests), [])
        self.assertEqual(self.rules(shell('pip install requests', 'Downloading requests-2.32.0-py3-none-any.whl'),
                                    requests), ['c:target-package-install'])
        # Quiet installs cannot be checked and count; pytest's "collecting" is not pip output.
        self.assertEqual(self.rules(shell('pip install -q requests && pytest', 'collecting ... collected 2 items'),
                                    requests), ['c:target-package-install'])


class ContaminationAuditTests(unittest.TestCase):
    def test_codex_streams_and_opencode_databases_are_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dataset = tmp / 'dataset.json'
            dataset.write_text(json.dumps([{'cve_id': 'CVE-2022-2024', 'repo': 'https://github.com/gogs/gogs'},
                                           {'cve_id': 'CVE-2018-20834', 'repo': 'https://github.com/isaacs/node-tar'}]))
            codex = tmp / 'codex/generation/sample_1/run/trajectories/00001-patcheval_CVE-2022-2024'
            codex.mkdir(parents=True)
            item = {'type': 'command_execution', 'exit_code': 0, 'aggregated_output': 'func Fixed() {}',
                    'command': "/bin/bash -lc 'curl -s https://raw.githubusercontent.com/gogs/gogs/v0.12.11/a.go'"}
            codex.joinpath('stdout.jsonl').write_text(json.dumps({'type': 'item.completed', 'item': item}) + '\n')
            clean = tmp / 'codex/generation/sample_0/run/trajectories/00000-patcheval_CVE-2018-20834'
            clean.mkdir(parents=True)
            clean.joinpath('stdout.jsonl').write_text('')
            replaced = tmp / 'codex/generation/sample_0/run/startup_reruns/x/replaced/trajectories/00000-patcheval_CVE-2018-20834'
            replaced.mkdir(parents=True)
            replaced.joinpath('stdout.jsonl').write_text(json.dumps({'type': 'item.completed', 'item': item}) + '\n')
            opencode = tmp / 'opencode/generation/sample_0/run/trajectories/00001-patcheval_CVE-2018-20834'
            (opencode / 'native').mkdir(parents=True)
            opencode.joinpath('stdout.jsonl').write_text('')
            con = sqlite3.connect(opencode / 'native/opencode.db')
            con.execute('CREATE TABLE part (data TEXT)')
            part = {'type': 'tool', 'tool': 'webfetch', 'state': {'status': 'completed', 'output': 'issue',
                    'input': {'url': 'https://github.com/isaacs/node-tar/issues/1'}}}
            con.execute('INSERT INTO part VALUES (?)', (json.dumps(part),))
            con.commit()
            con.close()
            episodes, evidence, summary = audit.audit({'codex': tmp / 'codex', 'opencode': tmp / 'opencode'}, dataset)
            self.assertEqual(sorted(episodes), ['codex|sample_1|CVE-2022-2024', 'opencode|sample_0|CVE-2018-20834'])
            self.assertEqual(summary['codex']['episodes'], 2)
            self.assertEqual(summary['opencode']['read_from_opencode-db'], 1)
            reviewed, _, _ = audit.audit({'codex': tmp / 'codex'}, dataset,
                                         {'false_positive': ['codex|sample_1|CVE-2022-2024']})
            self.assertEqual(reviewed, {})
            out = tmp / 'out'
            with patch('builtins.print'):
                audit.main(['--run', f'codex={tmp / "codex"}', '--run', f'opencode={tmp / "opencode"}',
                            '--dataset', str(dataset), '--out', str(out)])
            excluded = json.loads((out / 'excluded_cves.json').read_text())
            self.assertEqual(excluded['excluded_cves'], ['CVE-2018-20834', 'CVE-2022-2024'])
            self.assertTrue(re.search(r'CVE-2022-2024,a:target-upstream', (out / 'episodes.csv').read_text()))


if __name__ == '__main__':
    unittest.main()
