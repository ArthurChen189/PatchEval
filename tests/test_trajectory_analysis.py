import json
from pathlib import Path
import tempfile
import unittest

from scripts.infer.analyze_trajectories import analyze, compare_output, normalize_task


class TrajectoryAnalysisTests(unittest.TestCase):
    def archive(self, root, events, name='rollout.jsonl'):
        native = root / 'native/codex_sessions'
        native.mkdir(parents=True, exist_ok=True)
        path = native / name
        path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        (root / 'metadata.json').write_text('{"status":"generated"}')
        return path

    def test_mirrors_usage_and_complete_output(self):
        usage = {'input_tokens': 100, 'output_tokens': 20, 'reasoning_output_tokens': 7, 'total_tokens': 120}
        events = [
            {'type': 'session_meta', 'payload': {'id': 's'}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'Reasoning', 'id': 'r', 'summary_text': ['think'], 'raw_content': ['think']}}},
            {'type': 'response_item', 'payload': {'type': 'reasoning', 'id': 'r', 'summary': [{'text': 'think'}], 'content': [{'text': 'think'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'id': 'fc', 'call_id': 'call', 'arguments': '{}'}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'CommandExecution', 'id': 'call', 'stdout': 'full\noutput\n', 'stderr': 'warning'}}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'call', 'output': 'Chunk ID: a\nOutput:\nfull\noutput\n'}},
            {'type': 'token_usage_record', 'payload': {'response_id': 'response1', 'usage': usage, 'thread_token_usage': {'total_tokens': 9000}}},
            {'type': 'token_usage_record', 'payload': {'response_id': 'response1', 'usage': usage}},
            {'type': 'token_usage_record', 'payload': {'response_id': 'response2', 'usage': usage}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 9000}}}},
        ]
        for i, event in enumerate(events):
            event['ordinal'] = i
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / 'task'
            raw = self.archive(task, events)
            original = raw.read_bytes()
            # An identical copied rollout must not double any counters.
            self.archive(task, events, 'copy.jsonl')
            result = normalize_task(task)
            self.assertEqual(result['counts']['reasoning_items'], 1)
            self.assertEqual(result['counts']['tool_items'], 1)
            self.assertEqual(result['counts']['token_usage_records'], 2)
            self.assertEqual(result['token_usage']['total_tokens'], 240)
            self.assertEqual(result['token_usage']['output_tokens'], 40)
            self.assertEqual(result['token_usage']['reasoning_output_tokens'], 14)
            reasoning = next(r for r in result['items'] if r['kind'] == 'reasoning')
            self.assertEqual(reasoning['text'], 'think')
            tool = next(r for r in result['items'] if r['kind'] == 'tool')
            self.assertEqual(tool['output_comparisons'][0]['relationship'], 'wrapper_only')
            self.assertEqual(tool['variants'][1]['item']['stderr'], 'warning')
            summary = analyze(task, Path(tmp) / 'analysis')
            self.assertEqual(summary['completed_tasks'], 1)
            self.assertEqual(raw.read_bytes(), original)
            with self.assertRaises(ValueError):
                analyze(task, task / 'analysis')
            with self.assertRaises(FileExistsError):
                analyze(task, Path(tmp) / 'analysis')

    def test_equal_text_distinct_ids_and_sessions_not_collapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for session in ('a', 'b'):
                self.archive(root, [
                    {'type': 'session_meta', 'ordinal': 0, 'payload': {'id': session}},
                    *[{'type': 'response_item', 'ordinal': n + 1, 'payload': {'type': 'reasoning', 'id': identity, 'content': [{'text': 'same'}]}} for n, identity in enumerate(('r1', 'r2'))],
                ], session + '.jsonl')
            self.assertEqual(normalize_task(root)['counts']['reasoning_items'], 4)

    def test_conflicting_usage_and_invalid_json_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.archive(root, [
                {'type': 'token_usage_record', 'payload': {'response_id': 'a', 'usage': {'output_tokens': 5}}},
                {'type': 'token_usage_record', 'payload': {'response_id': 'a', 'usage': {'output_tokens': 10}}},
                {'type': 'token_usage_record', 'payload': {'response_id': 'b', 'thread_token_usage': {'output_tokens': 30}}},
            ])
            with path.open('a') as f:
                f.write('{partial')
            result = normalize_task(root)
            self.assertEqual(result['token_usage']['output_tokens'], 5)
            self.assertEqual(len(result['warnings']), 3)

    def test_file_change_and_custom_tool_share_call_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.archive(root, [
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'id': 'fc', 'call_id': 'call', 'name': 'apply_patch', 'input': 'patch'}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'FileChange', 'id': 'call', 'changes': {'file': 'diff'}}}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'call', 'output': 'Success'}},
            ])
            result = normalize_task(root)
            self.assertEqual(result['counts']['tool_items'], 1)
            self.assertEqual(len(result['items'][0]['variants']), 3)

    def test_user_ui_alias_requires_same_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.archive(root, [
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'id': 'model', 'content': [{'text': 'prompt'}], 'internal_chat_message_metadata_passthrough': {'turn_id': 't'}}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 't', 'item': {'type': 'UserMessage', 'id': 'ui', 'content': [{'text': 'prompt'}]}}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'other', 'item': {'type': 'UserMessage', 'id': 'other', 'content': [{'text': 'prompt'}]}}},
            ])
            result = normalize_task(root)
            self.assertEqual(result['counts']['message_items'], 2)
            self.assertEqual(result['items'][0]['aliases'], ['ui'])

    def test_output_differences_are_not_silently_discarded(self):
        self.assertEqual(compare_output('short', 'Chunk ID: a\nOutput:\nshort and long')['relationship'], 'function_output_contains_stdout')
        result = compare_output('complete output', 'Chunk ID: a\nOutput:\n... 20 tokens truncated ...')
        self.assertEqual(result['relationship'], 'different')
        self.assertTrue(result['truncation_marker'])
        self.assertEqual(compare_output('full text', 'Chunk ID: a\nOutput:\ntext')['relationship'], 'stdout_contains_function_output')


if __name__ == '__main__':
    unittest.main()
