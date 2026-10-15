"""冻结 test 集的两级只读诊断；prepare 不联网，execute 须另行明确授权。

不采图、不输入、不生成运行时认证或 adopted 证明。text_value 仍只是既有
factory/crop 协议诊断。总延迟是每个案例两次 HTTP 耗时之和，不是实机任务耗时。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
from time import monotonic, perf_counter_ns
from types import SimpleNamespace

import httpx


DEFAULT_SOURCE = Path('C:/Users/DesolateJix/.codex/worktrees/decision-api/agent-gui-runtime')
CHOICES = ('condition_met', 'condition_not_met', 'evidence_insufficient')
TIMEOUT = 12
RESERVE = 1_000_000


def _dependencies(source_root):
    root = Path(source_root).resolve()
    sys.path.insert(0, str(root))
    modules = {name: importlib.import_module(name) for name in (
        'scripts.run_decision_benchmark', 'scripts.decision_benchmark', 'scripts.decision_benchmark_budget',
        'app.judgment.openai_decisions', 'app.judgment.http_deadline')}
    if any(not Path(m.__file__).resolve().is_relative_to(root) for m in modules.values()):
        raise ValueError('cascade_source_import_conflict')
    return SimpleNamespace(runner=modules['scripts.run_decision_benchmark'],
        suite=modules['scripts.decision_benchmark'], budget=modules['scripts.decision_benchmark_budget'],
        provider=modules['app.judgment.openai_decisions'], deadline=modules['app.judgment.http_deadline'])


def ordinary_body(decision_body):
    """只转换冻结问题与输入，不接收主层答案或标注真值。"""
    if (type(decision_body) is not dict or decision_body.get('model') != 'gpt-6-luna'
            or type(decision_body.get('input')) is not list or len(decision_body['input']) != 1
            or type(decision_body.get('questions')) is not list or len(decision_body['questions']) != 1):
        raise ValueError('cascade_question_invalid')
    message, question = decision_body['input'][0], decision_body['questions'][0]
    if (type(message) is not dict or message.get('role') != 'user' or type(message.get('content')) is not list
            or type(question) is not dict or question.get('type') != 'choice' or question.get('name') != 'current_step'
            or [item.get('value') for item in question.get('choices', [])] != list(CHOICES)
            or any(type(part) is not dict or part.get('type') not in ('input_text', 'input_image')
                for part in message['content'])):
        raise ValueError('cascade_question_invalid')
    content = deepcopy(message['content'])
    content.append({'type': 'input_text', 'text': 'Question and allowed choices (JSON): '
        + json.dumps(question, ensure_ascii=False, sort_keys=True)
        + '\nOutput exactly one allowed choice value and nothing else. Do not output JSON, explanations, confidence or probabilities.'})
    return {'model': 'gpt-6-luna', 'input': [{'role': 'user', 'content': content}],
        'reasoning': {'effort': 'none'}, 'max_output_tokens': 32, 'store': False, 'service_tier': 'default'}


def _load_inputs(suite_path, variants_path, primary_run, variant_names, source_root, d):
    suite_path, variants_path, primary_run, source_root = map(lambda p: Path(p).resolve(),
        (suite_path, variants_path, primary_run, source_root))
    suite = d.suite.load_suite(suite_path)
    variants = d.runner._variants(variants_path, {case['category'] for case in suite['cases']})
    if (type(variant_names) is not list or not variant_names or len(variant_names) != len(set(variant_names))
            or any(type(name) is not str or name not in variants for name in variant_names)):
        raise ValueError('cascade_variants_invalid')
    run = d.suite.read_json(primary_run / 'run.json')
    if (type(run) is not dict or set(run) != {'schema', 'run_id', 'config', 'tasks'}
            or run['schema'] != 'decision_benchmark_run.v1'
            or type(run['run_id']) is not str or re.fullmatch('[0-9a-f]{32}', run['run_id']) is None
            or type(run['config']) is not dict):
        raise ValueError('cascade_primary_run_conflict')
    config = run['config']
    if config.get('split') != 'test':
        raise ValueError('cascade_test_required')
    if (config.get('manifest_sha256') != suite['manifest_sha256']
            or config.get('variants_sha256') != sha256(variants_path.read_bytes()).hexdigest()
            or Path(config.get('suite_path', '')).resolve() != suite_path
            or Path(config.get('variants_path', '')).resolve() != variants_path
            or config.get('endpoint') != d.provider.ENDPOINT or config.get('timeout_seconds') != 8
            or type(config.get('budget_limit')) is not int or not 0 <= config['budget_limit'] <= 5_000_000
            or config.get('reserve_tokens') != RESERVE):
        raise ValueError('cascade_primary_config_conflict')
    order = d.suite.task_order(suite, list(variants), 'test', config.get('repeats'), config.get('seed'))
    cases, rows, bodies, tasks = {c['id']: c for c in suite['cases']}, [], {}, []
    for index, (case_id, variant, repeat) in enumerate(order):
        body = d.suite.request_body(cases[case_id], variants[variant])
        request_hash = d.runner._digest(body)
        request_id = 'benchmark-' + sha256(f"{run['run_id']}:{index}".encode('ascii')).hexdigest()
        task = {'case': case_id, 'variant': variant, 'repeat': repeat,
            'request_id': request_id, 'request_sha256': request_hash}
        tasks.append(task)
        if d.suite.read_json(primary_run / 'requests' / (request_id + '.json')) != d.runner._request_record(body, request_hash):
            raise ValueError('cascade_primary_request_conflict')
        if d.suite.read_json(primary_run / 'dispatches' / (request_id + '.json')) != {**task, 'reserved_tokens': RESERVE}:
            raise ValueError('cascade_primary_dispatch_conflict')
        row = d.runner._received_row(d.runner._base_row(task, cases[case_id]),
            d.suite.read_json(primary_run / 'responses' / (request_id + '.json')), primary_run)
        rows.append(row)
        if variant in variant_names:
            bodies[request_id] = body
    if run['tasks'] != tasks or d.suite.read_json(primary_run / 'results.json') != rows:
        raise ValueError('cascade_primary_result_conflict')
    if not rows:
        raise ValueError('cascade_primary_empty')
    source_paths = ['scripts/run_decision_benchmark.py', 'scripts/decision_benchmark.py',
        'scripts/decision_benchmark_budget.py', 'app/judgment/openai_decisions.py', 'app/judgment/http_deadline.py']
    frozen = {'suite_path': str(suite_path), 'suite_sha256': suite['manifest_sha256'],
        'variants_path': str(variants_path), 'variants_sha256': sha256(variants_path.read_bytes()).hexdigest(),
        'primary_run': str(primary_run), 'primary_run_sha256': sha256((primary_run / 'run.json').read_bytes()).hexdigest(),
        'primary_results_sha256': sha256((primary_run / 'results.json').read_bytes()).hexdigest(),
        'primary_records_sha256': d.runner._digest(rows), 'variant_names': variant_names,
        'source_root': str(source_root), 'source_sha256': {name: sha256((source_root / name).read_bytes()).hexdigest()
            for name in source_paths}, 'runner_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'budget_path': str(Path(config['budget_path']).resolve()), 'budget_limit': config['budget_limit'],
        'reserve_tokens': RESERVE, 'primary_timeout_seconds': 8, 'ordinary_timeout_seconds': TIMEOUT,
        'ordinary_endpoint': d.provider.REVIEW_ENDPOINT, 'split': 'test'}
    return frozen, [row for row in rows if row['variant'] in variant_names], bodies, rows


def _review_required(row):
    return row['status'] == 'completed' and row['choice'] in ('condition_not_met', 'evidence_insufficient')


def _task(row, body, d):
    required = _review_required(row)
    request_hash = d.runner._digest(ordinary_body(body)) if required else None
    request_id = ('cascade-' + sha256((row['request_id'] + ':' + request_hash).encode('ascii')).hexdigest()
        if required else None)
    return {'case': row['case'], 'variant': row['variant'], 'repeat': row['repeat'],
        'primary_request_id': row['request_id'], 'primary_request_sha256': row['request_sha256'],
        'review_request_id': request_id, 'review_request_sha256': request_hash}


def _manifest(config, primary_rows, bodies, d):
    return {'schema': 'decision_cascade_diagnostic.v1', 'diagnostic_only': True, 'config': config,
        'tasks': [_task(row, bodies[row['request_id']], d) for row in primary_rows]}


def _verify_files(output, manifest, primary_rows, bodies, d, *, create=False):
    for row, task in zip(primary_rows, manifest['tasks']):
        path = output / 'stages' / 'primary' / (row['request_id'] + '.json')
        if create:
            d.runner._save(path, row, exclusive=True)
        elif d.suite.read_json(path) != row:
            raise ValueError('cascade_primary_copy_conflict')
        if task['review_request_id'] is None:
            continue
        data = d.runner._json_bytes(ordinary_body(bodies[row['request_id']]))
        path = output / 'requests' / (task['review_request_id'] + '.body')
        if create:
            d.runner._write(path, data, exclusive=True)
        elif path.read_bytes() != data:
            raise ValueError('cascade_request_conflict')


def prepare_cascade(suite_path, variants_path, primary_run, output_dir, *, variant_names,
        source_root=DEFAULT_SOURCE, resume=False):
    d, output = _dependencies(source_root), Path(output_dir).resolve()
    if type(resume) is not bool:
        raise ValueError('cascade_mode_invalid')
    with d.runner._run_lock(output):
        config, rows, bodies, _ = _load_inputs(suite_path, variants_path, primary_run, variant_names, source_root, d)
        manifest = _manifest(config, rows, bodies, d)
        existing = (output / 'run.json').exists()
        if existing:
            if not resume or d.suite.read_json(output / 'run.json') != manifest:
                raise ValueError('cascade_run_conflict')
        elif resume or any(path.name != '.runner.lock' for path in output.iterdir()):
            raise ValueError('cascade_run_conflict')
        _verify_files(output, manifest, rows, bodies, d, create=not existing)
        if not existing:
            d.runner._save(output / 'run.json', manifest, exclusive=True)
        return {'mode': 'prepared', 'diagnostic_only': True, 'primary_tasks': len(rows),
            'review_tasks': sum(_review_required(row) for row in rows), 'output_dir': str(output),
            'limitation': 'Frozen test-only protocol diagnostics; no action receipt, dispatch or runtime authentication'}


def _usage(raw, d):
    try:
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=d.runner._unique_object,
            parse_constant=d.runner._invalid_constant)
    except (UnicodeError, ValueError):
        return None, None
    try:
        return body, d.provider._usage_counts(body.get('usage') if type(body) is dict else None)
    except (ValueError, TypeError):
        return body, None


def _read_ordinary(task, output, d):
    request_id = task['review_request_id']
    path = output / 'responses' / (request_id + '.json')
    receipt = d.suite.read_json(path)
    fields = {'request_id', 'request_sha256', 'http_status', 'http_elapsed_ms', 'provider_request_id',
        'server_processing_ms', 'response_sha256', 'error'}
    if (type(receipt) is not dict or set(receipt) != fields or receipt['request_id'] != request_id
            or receipt['request_sha256'] != task['review_request_sha256']
            or type(receipt['http_elapsed_ms']) not in (int, float)
            or not math.isfinite(receipt['http_elapsed_ms']) or receipt['http_elapsed_ms'] < 0):
        raise ValueError('cascade_response_conflict')
    row = {**receipt, 'usage': None, 'choice': None, 'attempted': True}
    if receipt['response_sha256'] is None:
        if receipt['http_status'] is not None or receipt['error'] not in ('timeout', 'transport_error'):
            raise ValueError('cascade_response_conflict')
        return {**row, 'status': 'unknown'}
    raw = (output / 'responses' / (request_id + '.raw')).read_bytes()
    if (sha256(raw).hexdigest() != receipt['response_sha256'] or type(receipt['http_status']) is not int
            or not 100 <= receipt['http_status'] <= 599 or receipt['error'] not in (None, 'late_response', 'response_too_large')):
        raise ValueError('cascade_response_conflict')
    body, row['usage'] = _usage(raw, d) if len(raw) <= 65_536 else (None, None)
    if receipt['error'] is not None or receipt['http_status'] != 200:
        return {**row, 'status': 'error', 'error': receipt['error'] or 'http_status_' + str(receipt['http_status'])}
    try:
        answer, row['usage'] = d.provider._parse_ordinary_review_reply(body)
    except (ValueError, TypeError, KeyError):
        return {**row, 'status': 'error', 'error': 'invalid_reply'}
    return {**row, 'status': 'completed' if answer['type'] == 'choice' else 'refusal',
        'choice': answer['choice'], 'review_result': answer, 'error': None}


def _send(client, key, task, output, d):
    data = (output / 'requests' / (task['review_request_id'] + '.body')).read_bytes()
    if sha256(data).hexdigest() != task['review_request_sha256']:
        raise ValueError('cascade_request_conflict')
    started, deadline = perf_counter_ns(), monotonic() + TIMEOUT
    receipt = {'request_id': task['review_request_id'], 'request_sha256': task['review_request_sha256'],
        'http_status': None, 'http_elapsed_ms': None, 'provider_request_id': None,
        'server_processing_ms': None, 'response_sha256': None, 'error': None}
    try:
        response = client.post(d.provider.REVIEW_ENDPOINT, content=data, timeout=TIMEOUT, follow_redirects=False,
            extensions={'total_deadline': deadline}, headers={'Authorization': 'Bearer ' + key,
                'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                'X-Client-Request-Id': task['review_request_id']})
    except (httpx.TimeoutException, TimeoutError):
        receipt['http_elapsed_ms'] = (perf_counter_ns() - started) / 1_000_000
        receipt['error'] = 'timeout'
    except (httpx.HTTPError, OSError, ValueError):
        receipt['http_elapsed_ms'] = (perf_counter_ns() - started) / 1_000_000
        receipt['error'] = 'transport_error'
    else:
        receipt['http_elapsed_ms'] = (perf_counter_ns() - started) / 1_000_000
        late = monotonic() > deadline
        raw = response.content
        d.runner._write(output / 'responses' / (task['review_request_id'] + '.raw'), raw, exclusive=True)
        receipt.update(http_status=response.status_code, response_sha256=sha256(raw).hexdigest())
        provider_id = response.headers.get('x-request-id')
        if type(provider_id) is str and key not in provider_id and re.fullmatch('[A-Za-z0-9_-]{1,128}', provider_id):
            receipt['provider_request_id'] = provider_id
        processing = response.headers.get('openai-processing-ms')
        if processing is not None and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', processing):
            number = float(processing)
            if math.isfinite(number):
                receipt['server_processing_ms'] = number
        if len(raw) > 65_536:
            receipt['error'] = 'response_too_large'
        elif late:
            receipt['error'] = 'late_response'
    d.runner._save(output / 'responses' / (task['review_request_id'] + '.json'), receipt, exclusive=True)


def _combine(primary, ordinary):
    triggered = _review_required(primary)
    final = ordinary if triggered else primary
    status = final['status']
    choice = final.get('choice') if status == 'completed' else None
    if not triggered and primary.get('choice') != 'condition_met' and status == 'completed':
        raise ValueError('cascade_route_conflict')
    primary_ms, ordinary_ms = primary['http_elapsed_ms'], ordinary.get('http_elapsed_ms')
    elapsed = (primary_ms + ordinary_ms if triggered and ordinary_ms is not None else
        primary_ms if not triggered else None)
    return {key: primary[key] for key in ('case', 'variant', 'repeat', 'group_id', 'category', 'expected')} | {
        'primary': primary, 'ordinary': ordinary, 'status': status, 'choice': choice, 'http_elapsed_ms': elapsed,
        'primary_false_pass': primary['status'] == 'completed' and primary['choice'] == 'condition_met'
            and primary['expected'] != 'condition_met'}


def summarize_cascade(rows, *, source_root=DEFAULT_SOURCE, _groups=True):
    d = _dependencies(source_root)
    flat = []
    for row in rows:
        stages = [row['primary']] + ([row['ordinary']] if row['ordinary']['status'] != 'skipped' else [])
        usages = [stage['usage'] for stage in stages if stage.get('usage') is not None]
        flat.append({**row, 'usage': {key: sum(usage[key] for usage in usages)
            for key in ('input_tokens', 'output_tokens')}})
    result = d.suite.summarize(flat)
    primary_summary = d.suite.summarize([row['primary'] for row in rows])
    result['primary_exact_correct'] = primary_summary['exact_correct']
    result['primary_accuracy'] = primary_summary['accuracy']
    result['primary_false_passes'] = sum(bool(row['primary_false_pass']) for row in rows)
    result['review_triggered'] = sum(row['ordinary']['status'] != 'skipped' for row in rows)
    result['ordinary_dispatched'] = sum(bool(row['ordinary'].get('attempted')) for row in rows)
    result['unknown_usage_requests'] = sum(row['primary'].get('usage') is None for row in rows) + sum(
        row['ordinary'].get('usage') is None and row['ordinary']['status'] not in ('skipped', 'prepared', 'budget_exceeded')
        for row in rows)
    result['corrected_positive_rejections'] = sum(_review_required(row['primary'])
        and row['expected'] == row['choice'] == 'condition_met' for row in rows)
    result['new_false_accepts'] = sum(_review_required(row['primary']) and row['choice'] == 'condition_met'
        and row['expected'] != 'condition_met' for row in rows)
    result['latency_definition'] = 'Per-case primary HTTP plus ordinary HTTP; percentiles after summing. Not runtime wall time.'
    primary_usage = [row['primary'].get('usage') for row in rows if row['primary'].get('usage') is not None]
    ordinary_usage = [row['ordinary'].get('usage') for row in rows if row['ordinary'].get('usage') is not None]
    for prefix, usages in [('primary', primary_usage), ('ordinary', ordinary_usage)]:
        for field in ('input_tokens', 'output_tokens'):
            result[prefix + '_' + field] = sum(usage[field] for usage in usages)
    result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    result['cached_input_tokens'] = sum(usage.get('input_tokens_details', {}).get('cached_tokens', 0)
        for usage in primary_usage + ordinary_usage)
    result['reasoning_output_tokens'] = sum(usage.get('output_tokens_details', {}).get('reasoning_tokens', 0)
        for usage in primary_usage + ordinary_usage)
    result['primary_http_latency'] = d.suite._latency([row['primary']['http_elapsed_ms'] for row in rows])
    result['ordinary_http_latency'] = d.suite._latency([row['ordinary'].get('http_elapsed_ms') for row in rows])
    if _groups:
        for key in ('category', 'variant'):
            result['by_' + key] = {name: summarize_cascade([row for row in rows if row[key] == name],
                source_root=source_root, _groups=False) for name in sorted({row[key] for row in rows})}
    else:
        result.pop('by_category', None)
    return result


def _check_primary_budget(rows, snapshot):
    for row in rows:
        entry = snapshot['requests'].get(row['request_id'])
        if (entry is None or entry['amount'] != RESERVE or entry['request_sha256'] != row['request_sha256']):
            raise ValueError('cascade_primary_budget_conflict')
        if row['usage'] is not None and entry['settlement'] != {
                'input_tokens': row['usage']['input_tokens'], 'output_tokens': row['usage']['output_tokens'],
                'response_sha256': row['response_sha256']}:
            raise ValueError('cascade_primary_budget_conflict')
        if row['usage'] is None and entry['settlement'] is not None:
            raise ValueError('cascade_primary_budget_conflict')


def execute_cascade(output_dir, *, allow_execute=False, api_key_env='OPENAI_API_KEY', client=None):
    if allow_execute is not True:
        raise ValueError('cascade_execute_not_authorized')
    if type(api_key_env) is not str or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', api_key_env) is None:
        raise ValueError('cascade_key_environment_invalid')
    output = Path(output_dir).resolve()
    bootstrap = json.loads((output / 'run.json').read_text(encoding='utf-8'))
    d = _dependencies(bootstrap['config']['source_root'])
    with d.runner._run_lock(output):
        manifest = d.suite.read_json(output / 'run.json')
        saved = manifest['config']
        config, primary_rows, bodies, all_primary_rows = _load_inputs(saved['suite_path'], saved['variants_path'],
            saved['primary_run'], saved['variant_names'], saved['source_root'], d)
        if manifest != _manifest(config, primary_rows, bodies, d):
            raise ValueError('cascade_run_conflict')
        _verify_files(output, manifest, primary_rows, bodies, d)
        budget_path = Path(config['budget_path'])
        if not budget_path.is_file():
            raise ValueError('cascade_budget_missing')
        budget = d.budget.TokenBudget(budget_path, limit=config['budget_limit'])
        _check_primary_budget(all_primary_rows, budget.snapshot())
        rows, owned_client, key = [], False, None
        try:
            for task, primary in zip(manifest['tasks'], primary_rows):
                request_id = task['review_request_id']
                ordinary = {'status': 'skipped', 'choice': None, 'usage': None, 'attempted': False}
                if request_id is not None:
                    entry = budget.snapshot()['requests'].get(request_id)
                    if entry is not None and (entry['amount'] != RESERVE
                            or entry['request_sha256'] != task['review_request_sha256']):
                        raise ValueError('cascade_budget_response_conflict')
                    response_path = output / 'responses' / (request_id + '.json')
                    dispatch_path = output / 'dispatches' / (request_id + '.json')
                    raw_path = output / 'responses' / (request_id + '.raw')
                    dispatch = {**task, 'reserved_tokens': RESERVE}
                    if dispatch_path.exists() and d.suite.read_json(dispatch_path) != dispatch:
                        raise ValueError('cascade_dispatch_conflict')
                    if response_path.exists() or dispatch_path.exists() or raw_path.exists():
                        if entry is None:
                            raise ValueError('cascade_budget_response_conflict')
                    if response_path.exists():
                        ordinary = _read_ordinary(task, output, d)
                    elif entry is not None:
                        ordinary = {'status': 'unknown', 'choice': None, 'usage': None,
                            'attempted': dispatch_path.exists(), 'error': 'reserved_without_complete_response'}
                    else:
                        key = os.environ.get(api_key_env)
                        if not key or not key.strip():
                            raise ValueError('cascade_key_missing')
                        try:
                            fresh = budget.reserve(request_id, RESERVE, task['review_request_sha256'])
                        except d.budget.BudgetExceeded:
                            ordinary = {'status': 'budget_exceeded', 'choice': None, 'usage': None, 'attempted': False}
                        else:
                            if not fresh:
                                ordinary = {'status': 'unknown', 'choice': None, 'usage': None,
                                    'attempted': False, 'error': 'reserved_without_complete_response'}
                            else:
                                d.runner._save(dispatch_path, dispatch, exclusive=True)
                                if client is None:
                                    client = httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False,
                                        transport=d.deadline.DeadlineHTTPTransport(limits=httpx.Limits(keepalive_expiry=60)))
                                    owned_client = True
                                _send(client, key, task, output, d)
                                ordinary = _read_ordinary(task, output, d)
                    if ordinary.get('usage') is not None:
                        try:
                            budget.settle(request_id, ordinary['usage']['input_tokens'], ordinary['usage']['output_tokens'],
                                ordinary['response_sha256'])
                        except d.budget.BudgetOverrun:
                            ordinary = {**ordinary, 'status': 'error', 'choice': None, 'error': 'budget_overrun'}
                    d.runner._save(output / 'stages' / 'ordinary' / (request_id + '.json'), ordinary)
                rows.append(_combine(primary, ordinary))
                d.runner._save(output / 'results.json', rows)
            result = {'mode': 'executed', 'diagnostic_only': True, 'rows': rows,
                'summary': summarize_cascade(rows, source_root=config['source_root']), 'budget': budget.snapshot(),
                'limitations': ['No runtime action/receipt authentication', 'Primary false passes bypass ordinary review',
                    'text_value is factory/crop protocol only', 'Historical primary plus secondary HTTP, not continuous wall time']}
            for name, value in [('results.json', rows), ('summary.json', result['summary']), ('budget-snapshot.json', result['budget'])]:
                d.runner._save(output / name, value)
            return result
        finally:
            key = None
            if owned_client:
                client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    prepare = sub.add_parser('prepare', help='离线冻结，仅允许已完成的 test run')
    prepare.add_argument('--suite', required=True)
    prepare.add_argument('--variants', required=True)
    prepare.add_argument('--primary-run', required=True)
    prepare.add_argument('--select-variants', nargs='+', required=True)
    prepare.add_argument('--output-dir', required=True)
    prepare.add_argument('--source-root', default=str(DEFAULT_SOURCE))
    prepare.add_argument('--resume', action='store_true')
    execute = sub.add_parser('execute', help='必须经 Main 审核 prepare 后单独批准')
    execute.add_argument('--output-dir', required=True)
    execute.add_argument('--allow-execute', action='store_true')
    execute.add_argument('--api-key-env', default='OPENAI_API_KEY')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'prepare':
            result = prepare_cascade(args.suite, args.variants, args.primary_run, args.output_dir,
                variant_names=args.select_variants, source_root=args.source_root, resume=args.resume)
        else:
            result = execute_cascade(args.output_dir, allow_execute=args.allow_execute, api_key_env=args.api_key_env)
    except (ValueError, OSError, KeyError, TypeError) as error:
        code = str(error)
        if re.fullmatch('(cascade|benchmark)_[a-z0-9_]+', code) is None:
            code = 'cascade_failed'
        print(code + ': inspect immutable artifacts and token journal; do not resend unknown requests', file=sys.stderr)
        return 2
    print(json.dumps({key: value for key, value in result.items() if key not in ('rows', 'budget')}, ensure_ascii=False))
    return 1 if args.mode == 'execute' and result['summary']['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
