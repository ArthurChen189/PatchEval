"""Read-only Codex archive analysis; raw logs are never rewritten."""

from collections import Counter
import hashlib
import json
from pathlib import Path


TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def text_parts(parts):
    if isinstance(parts, str):
        return parts
    return ''.join(p if isinstance(p, str) else p.get('text', '') for p in (parts or []))


def compare_output(stdout, output):
    """Compare exact text and the body of the harness's formatted tool result."""
    if not isinstance(output, str):
        return {'relationship': 'non_text_output'}
    body = output
    if output.startswith('Chunk ID: ') and '\nOutput:\n' in output:
        body = output.split('\nOutput:\n', 1)[1]
    if stdout == output:
        relation = 'identical'
    elif stdout == body:
        relation = 'wrapper_only'
    elif stdout and stdout in body:
        relation = 'function_output_contains_stdout'
    elif body and body in stdout:
        relation = 'stdout_contains_function_output'
    else:
        relation = 'different'
    return {'relationship': relation, 'stdout_chars': len(stdout),
            'function_output_chars': len(output), 'function_body_chars': len(body),
            'truncation_marker': any(s in stdout.lower() or s in output.lower()
                                     for s in ('tokens truncated', 'output truncated', 'chars truncated'))}


def normalize_task(task):
    task = Path(task)
    records, usage, manifest, warnings = {}, {}, [], []
    seen_events = set()
    counts = Counter()
    files = sorted((task / 'native' / 'codex_sessions').rglob('*.jsonl'))
    for path in files:
        data = path.read_bytes()
        relative = str(path.relative_to(task))
        manifest.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        session = relative
        for number, line in enumerate(data.splitlines(), 1):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                warnings.append(f'{relative}:{number}: invalid JSON; skipped')
                continue
            payload = event.get('payload', {})
            kind = event.get('type')
            source = {'path': relative, 'line': number, 'ordinal': event.get('ordinal')}
            if kind == 'session_meta':
                session = payload.get('session_id') or payload.get('id') or session
            # Ordinals are session-local. Content equality alone is not identity.
            event_id = event.get('event_id') or event.get('id')
            if event_id is None:
                event_id = event.get('ordinal')
            if event_id is not None:
                key = (session, str(event_id))
                if key in seen_events:
                    counts['duplicate_events'] += 1
                    continue
                seen_events.add(key)
            if kind == 'token_usage_record':
                identity = payload.get('response_id') or payload.get('id') or event_id
                if identity is None:
                    identity = f'{relative}:{number}'
                    warnings.append(f'{relative}:{number}: usage lacks stable ID')
                key = (session, str(identity))
                values = payload.get('usage')
                if not isinstance(values, dict):
                    warnings.append(f'{relative}:{number}: missing per-response usage; cumulative counters ignored')
                    continue
                if key in usage:
                    counts['duplicate_usage_records'] += 1
                    usage[key]['sources'].append(source)
                    if usage[key]['usage'] != values:
                        warnings.append(f'{relative}:{number}: conflicting usage for {identity}; first record retained')
                else:
                    usage[key] = {'session_id': session, 'response_id': identity,
                                  'usage': values, 'sources': [source]}
                continue
            if kind == 'event_msg' and payload.get('type') == 'token_count':
                counts['ignored_token_count_events'] += 1
                continue
            item = payload if kind == 'response_item' else payload.get('item', {})
            if kind not in ('response_item', 'event_msg') or not item:
                continue
            if kind == 'event_msg' and payload.get('type') != 'item_completed':
                continue
            typ = item.get('type')
            group = {'Reasoning': 'reasoning', 'reasoning': 'reasoning',
                     'AgentMessage': 'message', 'UserMessage': 'message', 'message': 'message',
                     'CommandExecution': 'tool', 'FileChange': 'tool', 'function_call': 'tool',
                     'function_call_output': 'tool', 'custom_tool_call': 'tool',
                     'custom_tool_call_output': 'tool'}.get(typ, typ)
            identity = item.get('call_id') or item.get('id') or event_id or f'{relative}:{number}'
            key = (session, group, str(identity))
            record = records.setdefault(key, {'session_id': session, 'kind': group,
                                               'id': identity, 'turn_id': payload.get('turn_id') or
                                               item.get('internal_chat_message_metadata_passthrough', {}).get('turn_id'),
                                               'variants': []})
            variant = next((v for v in record['variants'] if v['item'] == item and v['source_type'] == kind), None)
            if variant:
                variant['sources'].append(source)
            else:
                record['variants'].append({'source_type': kind, 'item': item, 'sources': [source]})
    # Codex assigns a separate UI ID to UserMessage. Correlate only an
    # unambiguous, exact user-message mirror within the same session and turn.
    for key, record in list(records.items()):
        if record['kind'] != 'message' or record['variants'][0]['item']['type'] != 'UserMessage':
            continue
        item = record['variants'][0]['item']
        candidates = [r for r in records.values() if r is not record
                      and r['session_id'] == record['session_id']
                      and r['turn_id'] and r['turn_id'] == record['turn_id']
                      and any(v['source_type'] == 'response_item'
                              and v['item'].get('role') == 'user'
                              and text_parts(v['item'].get('content')) == text_parts(item.get('content'))
                              for v in r['variants'])]
        if len(candidates) == 1:
            candidates[0]['variants'].extend(record['variants'])
            candidates[0].setdefault('aliases', []).append(record['id'])
            candidates[0]['alias_basis'] = 'unique exact user message in same session and turn'
            del records[key]
    for record in records.values():
        variants = record['variants']
        # Prefer model response items over UI event mirrors. Never concatenate aliases.
        ordered = sorted(variants, key=lambda v: v['source_type'] != 'response_item')
        if record['kind'] in ('reasoning', 'message'):
            item = ordered[0]['item']
            if record['kind'] == 'reasoning':
                record['text'] = text_parts(item.get('content') or item.get('raw_content') or
                                            item.get('summary') or item.get('summary_text'))
            else:
                record['text'] = text_parts(item.get('content'))
                record['role'] = item.get('role') or ('user' if item['type'] == 'UserMessage' else 'assistant')
        if record['kind'] == 'tool':
            commands = [v for v in variants if v['item']['type'] == 'CommandExecution']
            outputs = [v for v in variants if v['item']['type'] in ('function_call_output', 'custom_tool_call_output')]
            record['output_comparisons'] = [
                {'command_variant': variants.index(c), 'output_variant': variants.index(o),
                 **compare_output(c['item'].get('stdout', ''), o['item'].get('output')),
                 'stderr_chars': len(c['item'].get('stderr', '')),
                 'aggregate_comparison': compare_output(c['item'].get('aggregated_output', ''), o['item'].get('output'))}
                for c in commands for o in outputs]
            # Both exact stdout/stderr and exact model-visible output remain in variants.
            # Neither is asserted to be complete when the harness itself truncated it.
    totals = {field: sum(r['usage'].get(field, 0) or 0 for r in usage.values()) for field in TOKEN_FIELDS}
    if not files:
        warnings.append('No archived Codex native sessions; no CLI/native merging or token estimate attempted')
    counts.update({f'{kind}_items': count for kind, count in Counter(r['kind'] for r in records.values()).items()})
    counts['token_usage_records'] = len(usage)
    for name in ('stdout.jsonl', 'stderr.txt', 'prompt.txt', 'metadata.json'):
        path = task / name
        if path.is_file():
            data = path.read_bytes()
            manifest.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    return {'schema_version': 1, 'task': task.name, 'sources': manifest,
            'cli_events_path': str(task / 'stdout.jsonl'),
            'counts': dict(counts), 'token_usage': totals,
            'token_usage_policy': 'Sum unique per-response usage only; reasoning_output_tokens is a subset of output_tokens. Cumulative counters and token_count mirrors are excluded.',
            'warnings': warnings, 'usage_records': list(usage.values()), 'items': list(records.values())}


def analyze(input_dir, output_dir):
    source, output = Path(input_dir).resolve(), Path(output_dir).resolve()
    tasks = [source] if (source / 'metadata.json').is_file() else sorted(source.glob('**/trajectories/*'))
    tasks = [p for p in tasks if p.is_dir() and (p / 'metadata.json').is_file()]
    if not tasks:
        raise ValueError(f'No archived trajectory tasks found beneath {source}')
    if output == source or source.is_relative_to(output) or any(output.is_relative_to(t) for t in tasks):
        raise ValueError('analysis.output_dir must be separate from raw trajectory directories')
    output.mkdir(parents=True, exist_ok=False)
    summaries = []
    for index, task in enumerate(tasks):
        metadata = json.loads((task / 'metadata.json').read_text())
        if metadata.get('status') == 'running':
            continue
        result = normalize_task(task)
        filename = f'{index:05d}-{task.name}.json'
        (output / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        comparisons = Counter(c['relationship'] for r in result['items'] for c in r.get('output_comparisons', []))
        summaries.append({'task': str(task), 'normalized': filename, 'counts': result['counts'],
                          'token_usage': result['token_usage'], 'output_comparisons': dict(comparisons),
                          'warnings': result['warnings']})
    totals, comparisons, counts = Counter(), Counter(), Counter()
    for task in summaries:
        totals.update(task['token_usage'])
        comparisons.update(task['output_comparisons'])
        counts.update(task['counts'])
    summary = {'completed_tasks': len(summaries), 'token_usage': dict(totals),
               'counts': dict(counts), 'output_comparisons': dict(comparisons), 'tasks': summaries}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
