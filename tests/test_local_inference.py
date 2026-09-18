import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INFER = ROOT / 'scripts/infer'


def module(name):
    spec = importlib.util.spec_from_file_location(name, INFER / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


configs = module('configure_harnesses')
checks = module('check_server')


class ConfigTests(unittest.TestCase):
    def test_configs_match_adapter_layout_and_limits(self):
        with tempfile.TemporaryDirectory(prefix='local inference ') as directory:
            output = configs.configure(directory, 'http://172.17.0.1:30000/v1/', 'Qwen/Qwen3.8-27B', 65536)
            codex = tomllib.loads((output / 'codex/config.toml').read_text())
            self.assertEqual(codex['model_provider'], 'vllm')
            self.assertNotIn('profiles', codex)
            self.assertEqual(codex['model_providers']['vllm']['wire_api'], 'responses')
            self.assertEqual(codex['model_auto_compact_token_limit'], 57344)
            self.assertEqual((output / 'codex/local.config.toml').read_text(), (output / 'codex/config.toml').read_text())
            opencode = json.loads((output / 'opencode/config/opencode/opencode.json').read_text())
            self.assertEqual(opencode['model'], 'vllm/Qwen/Qwen3.8-27B')
            self.assertTrue((output / 'opencode/data').is_dir())
            with self.assertRaises(FileExistsError):
                configs.configure(directory, 'http://host/v1', 'different', 65536)
            self.assertEqual(tomllib.loads((output / 'codex/config.toml').read_text()), codex)
            configs.configure(directory, 'http://host/v1', 'different', 65536, force=True)

    def test_invalid_configuration_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new'
            for url in ('file:///tmp/v1', 'http://user:secret@host/v1', 'http://host', 'http://host/v1?key=x'):
                with self.assertRaises(ValueError):
                    configs.configure(output, url, 'model', 65536)
            self.assertFalse(output.exists())


class ProtocolTests(unittest.TestCase):
    def test_check_credentials_precedence_and_fallback(self):
        for environment, expected in (({'VLLM_API_KEY': 'new', 'SGLANG_API_KEY': 'old'}, 'Bearer new'),
                                      ({'SGLANG_API_KEY': 'old'}, 'Bearer old'), ({}, None)):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                 patch.object(checks.urllib.request, 'urlopen') as request:
                checks.Client('http://host/v1', 300).request('/models')
                self.assertEqual(request.call_args.args[0].get_header('Authorization'), expected)

    def test_sse_comments_crlf_and_done(self):
        stream = io.BytesIO(b': ping\r\nevent: delta\r\ndata: {"delta":"ok"}\r\n\r\ndata: [DONE]\n\n')
        self.assertEqual(list(checks.sse_events(stream)), [{'delta': 'ok'}])

    def test_chat_reassembles_arguments_and_sends_result(self):
        class Fake:
            def stream(self, endpoint, payload):
                if len(payload['messages']) == 1:
                    yield {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call1', 'function': {'name': 'lookup_code', 'arguments': '{'}}]}}]}
                    yield {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': None, 'function': {'name': None, 'arguments': '"key":"verification"}'}}]}}]}
                else:
                    assert payload['messages'][-1]['tool_call_id'] == 'call1'
                    assert payload['messages'][-1]['content'] == checks.RESULT
                    yield {'choices': [{'delta': {'content': checks.RESULT}}]}
        checks.chat(Fake(), 'model')

    def test_responses_replays_call_and_result_without_server_state(self):
        class Fake:
            def stream(self, endpoint, payload):
                assert payload['store'] is False
                if len(payload['input']) == 1:
                    yield {'type': 'response.completed', 'response': {'output': [
                        {'type': 'function_call', 'call_id': 'c1', 'name': 'lookup_code', 'arguments': '{"key":"verification"}'}]}}
                else:
                    assert payload['input'][-1]['call_id'] == 'c1'
                    assert payload['input'][-1]['output'] == checks.RESULT
                    yield {'type': 'response.output_text.delta', 'delta': checks.RESULT}
                    yield {'type': 'response.completed'}
        checks.responses(Fake(), 'model')

    def test_plain_text_is_not_accepted_as_tool_support(self):
        class Fake:
            def stream(self, endpoint, payload):
                yield {'choices': [{'delta': {'content': 'lookup_code()'}}]}
        with self.assertRaisesRegex(ValueError, 'structured'):
            checks.chat(Fake(), 'model')
        with self.assertRaisesRegex(ValueError, 'never completed'):
            checks.responses(Fake(), 'model')

    def test_unreachable_server_fails_clearly(self):
        with patch.object(checks.Client, 'request', side_effect=OSError('connection refused')):
            self.assertEqual(checks.run_checks('http://host/v1', 'model'), 1)


if __name__ == '__main__':
    unittest.main()
