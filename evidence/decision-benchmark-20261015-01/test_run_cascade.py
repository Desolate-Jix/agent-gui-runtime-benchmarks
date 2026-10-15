"""两级诊断执行器的离线自测；合成夹具不是实际截图验收。"""
from copy import deepcopy
import base64
from hashlib import sha256
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys

import httpx
from PIL import Image
import pytest

SOURCE = Path('C:/Users/DesolateJix/.codex/worktrees/decision-api/agent-gui-runtime')
sys.path.insert(0, str(SOURCE))
from scripts import run_decision_benchmark as primary_runner
from scripts.decision_benchmark import load_suite, request_body
from scripts.decision_benchmark_budget import TokenBudget


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def cascade():
    path = Path(__file__).with_name('run_cascade.py')
    if not path.is_file():
        pytest.skip('implementation not yet written')
    spec = importlib.util.spec_from_file_location('external_cascade', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_implementation_is_available():
    assert Path(__file__).with_name('run_cascade.py').is_file(), 'missing cascade implementation'


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('CASCADE_TEST_KEY', 'offline-private-key')
    def make(answers=None, variants=None):
        answers = answers or {'passed': 'condition_met', 'wrongpass': 'condition_met',
            'rejected': 'condition_not_met', 'rescued': 'condition_not_met', 'unclear': 'evidence_insufficient'}
        variants = variants or {'baseline': {}, 'frozen_mix': {'graphic': 'Check {target}.'}}
        choices = ('condition_met', 'condition_not_met', 'evidence_insufficient')
        cases = []
        for name in answers:
            image = tmp_path / (name + '.png')
            Image.new('RGB', (16, 12), 'cyan').save(image)
            body = {'model': 'gpt-6-luna', 'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'case:' + name},
                {'type': 'input_image', 'image_url': image.name, 'detail': 'high'}]}],
                'questions': [{'type': 'choice', 'name': 'current_step', 'instructions': 'Check ' + name,
                    'choices': [{'value': c, 'description': 'visible-' + c} for c in choices]}]}
            request = tmp_path / (name + '.json')
            save(request, body)
            asset = lambda p: {'path': p.name, 'sha256': sha256(p.read_bytes()).hexdigest()}
            expected = ('condition_not_met' if name in ('wrongpass', 'rejected') else
                'evidence_insufficient' if name == 'unclear' else 'condition_met')
            cases.append({'id': name, 'group_id': 'synthetic-offline', 'split': 'test', 'category': 'graphic',
                'expected': expected, 'truth': {'annotator': 'fixture-only', 'reason': 'secret-truth'},
                'provenance': {'capture_id': 'fixture-capture', 'session_id': 'fixture-session', 'kind': 'real_capture'},
                'source': asset(image), 'image': asset(image), 'request': asset(request),
                'question_data': {'target': name}})
        suite, variant_file = tmp_path / 'suite.json', tmp_path / 'variants.json'
        save(suite, {'schema': 'decision_benchmark.v1', 'suite_id': 'synthetic-offline', 'cases': cases})
        save(variant_file, variants)
        primary, budget = tmp_path / 'primary', tmp_path / 'budget.jsonl'
        def handle(request):
            body = json.loads(request.content)
            name = body['input'][0]['content'][0]['text'].split(':')[1]
            answer = answers[name]
            item = {'type': 'refusal', 'name': 'current_step'} if answer == 'refusal' else {
                'type': 'choice', 'name': 'current_step', 'choice': answer, 'confidence': 1.0,
                'probabilities': [{'value': c, 'probability': float(c == answer)} for c in choices]}
            return httpx.Response(200, json={'model': 'gpt-6-luna', 'answers': [item],
                'usage': {'input_tokens': 31, 'output_tokens': 1, 'total_tokens': 32}},
                headers={'x-request-id': 'primary-offline'})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            primary_runner.run_benchmark(suite, variant_file, primary, budget, split='test', execute=True,
                api_key_env='CASCADE_TEST_KEY', client=client)
        return {'suite_path': suite, 'variants_path': variant_file, 'primary_run': primary,
            'output_dir': tmp_path / 'cascade', 'variant_names': list(variants), 'source_root': SOURCE}
    return make


def reply(choice='condition_not_met'):
    return {'model': 'gpt-6-luna', 'status': 'completed', 'error': None, 'incomplete_details': None,
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed', 'phase': 'final_answer',
            'content': [{'type': 'output_text', 'text': choice, 'annotations': []}]}],
        'usage': {'input_tokens': 20, 'output_tokens': 1, 'total_tokens': 21}}


def test_prepare_preserves_content_questions_and_never_reads_key_or_sends(cascade, inputs, monkeypatch):
    args = inputs()
    monkeypatch.delenv('CASCADE_TEST_KEY')
    monkeypatch.setattr(httpx, 'Client', lambda **kw: pytest.fail('prepare must be offline'))
    result = cascade.prepare_cascade(**args)
    assert result['review_tasks'] == 6 and result['primary_tasks'] == 10
    manifest = json.loads((args['output_dir'] / 'run.json').read_text(encoding='utf-8'))
    suite = load_suite(args['suite_path'])
    variants = json.loads(args['variants_path'].read_text(encoding='utf-8'))
    cases = {c['id']: c for c in suite['cases']}
    for task in manifest['tasks']:
        if task['review_request_id'] is None:
            assert task['case'] in ('passed', 'wrongpass')
            continue
        raw = (args['output_dir'] / 'requests' / (task['review_request_id'] + '.body')).read_bytes()
        body = json.loads(raw)
        original = request_body(cases[task['case']], variants[task['variant']])
        assert body['input'][0]['content'][:-1] == original['input'][0]['content']
        assert body['input'][0]['content'][-1]['text'] == ('Question and allowed choices (JSON): '
            + json.dumps(original['questions'][0], ensure_ascii=False, sort_keys=True)
            + '\nOutput exactly one allowed choice value and nothing else. Do not output JSON, explanations, confidence or probabilities.')
        assert all(secret not in raw.decode('utf-8') for secret in ('secret-truth', 'fixture-only', '"expected"', '"answers"'))
        assert sha256(raw).hexdigest() == task['review_request_sha256']
    with pytest.raises(ValueError, match='execute_not_authorized'):
        cascade.execute_cascade(args['output_dir'])


def test_execute_only_nonpass_persists_before_send_and_resumes_without_replay(cascade, inputs):
    args = inputs()
    cascade.prepare_cascade(**args)
    calls = []
    def handle(request):
        body = json.loads(request.content)
        request_id = request.headers['X-Client-Request-Id']
        assert request.url == 'https://api.openai.com/v1/responses'
        assert request.content == (args['output_dir'] / 'requests' / (request_id + '.body')).read_bytes()
        assert (args['output_dir'] / 'dispatches' / (request_id + '.json')).is_file()
        budget = TokenBudget(args['output_dir'].parent / 'budget.jsonl').snapshot()
        assert budget['requests'][request_id]['settlement'] is None
        assert 0 < request.extensions['timeout']['read'] <= 12
        assert request.extensions['total_deadline'] > 0
        calls.append(request_id)
        name = body['input'][0]['content'][0]['text'].split(':')[1]
        choice = {'rejected': 'condition_not_met', 'rescued': 'condition_met', 'unclear': 'evidence_insufficient'}[name]
        return httpx.Response(200, json=reply(choice), headers={'x-request-id': 'ordinary-offline'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
        repeated = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert len(calls) == 6
    assert result['rows'] == repeated['rows']
    assert result['summary']['exact_correct'] == 8 and result['summary']['false_accepts'] == 2
    assert result['summary']['primary_false_passes'] == 2
    assert result['summary']['primary_exact_correct'] == 6 and result['summary']['primary_accuracy'] == .6
    assert result['summary']['input_tokens'] == 430 and result['summary']['output_tokens'] == 16
    assert result['budget']['spent'] == 446 and result['budget']['reserved'] == 0
    assert result['diagnostic_only'] is True


def test_invalid_ordinary_reply_keeps_raw_and_settles_known_usage(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply('bad-option')))) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    row = result['rows'][0]
    assert row['status'] == 'error' and row['choice'] is None
    assert row['ordinary']['usage']['total_tokens'] == 21
    assert result['budget']['spent'] == 53 and result['budget']['reserved'] == 0
    assert result['summary']['exact_correct'] == 0
    assert len(list((args['output_dir'] / 'responses').glob('*.raw'))) == 1


def test_primary_refusal_never_triggers_ordinary(cascade, inputs):
    args = inputs({'refused': 'refusal'}, {'baseline': {}})
    result = cascade.prepare_cascade(**args)
    assert result['review_tasks'] == 0
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('must not send'))) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert result['rows'][0]['status'] == 'refusal' and result['summary']['exact_correct'] == 0


@pytest.mark.parametrize('crash', [False, True])
def test_unknown_and_crash_resume_never_resend_or_release_reservation(cascade, inputs, crash):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    def fail(request):
        if crash:
            raise KeyboardInterrupt()
        raise httpx.ReadTimeout('offline-private-key must never escape')
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        if crash:
            with pytest.raises(KeyboardInterrupt):
                cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
        else:
            cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('unknown replay'))) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert result['rows'][0]['status'] == 'unknown'
    assert result['budget']['reserved'] == 1_000_000 and result['budget']['spent'] == 32
    assert result['summary']['exact_correct'] == 0
    assert result['summary']['by_variant']['baseline']['unknown_usage_requests'] == 1
    assert result['summary']['by_category']['graphic']['unknown_usage_requests'] == 1
    assert 'offline-private-key' not in json.dumps(result)


def test_budget_exhaustion_prevents_http(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    budget = TokenBudget(args['output_dir'].parent / 'budget.jsonl')
    budget.reserve('unrelated-unknown', 4_000_000, 'a' * 64)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('over budget'))) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert result['rows'][0]['status'] == 'budget_exceeded'
    assert result['budget']['reserved'] == 4_000_000 and result['summary']['exact_correct'] == 0


@pytest.mark.parametrize('artifact', ['raw', 'result', 'request'])
def test_primary_frozen_artifact_conflicts_fail_closed(cascade, inputs, artifact):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    root = args['primary_run']
    if artifact == 'raw':
        next((root / 'responses').glob('*.raw')).write_bytes(b'{}')
    elif artifact == 'result':
        rows = json.loads((root / 'results.json').read_text(encoding='utf-8'))
        rows[0]['choice'] = 'condition_met'
        save(root / 'results.json', rows)
    else:
        path = next((root / 'requests').glob('*.json'))
        record = json.loads(path.read_text(encoding='utf-8'))
        record['body']['questions'][0]['instructions'] = 'different question'
        save(path, record)
    with pytest.raises(ValueError, match='conflict'):
        cascade.prepare_cascade(**args)


def test_only_heldout_test_is_eligible(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    path = args['primary_run'] / 'run.json'
    run = json.loads(path.read_text(encoding='utf-8'))
    run['config']['split'] = 'train'
    save(path, run)
    with pytest.raises(ValueError, match='test_required'):
        cascade.prepare_cascade(**args)


def test_run_lock_rejects_concurrent_prepare(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    with primary_runner._run_lock(args['output_dir']):
        with pytest.raises(ValueError, match='run_busy'):
            cascade.prepare_cascade(**args)


def test_conversion_matches_actual_production_provider_body(cascade, monkeypatch):
    from app.judgment.openai_decisions import OpenAIDecisionsProvider
    monkeypatch.setenv('CASCADE_TEST_KEY', 'offline-private-key')
    stream = BytesIO()
    Image.new('RGB', (64, 36), 'cyan').save(stream, format='PNG')
    payload = {'schema_version': 'decision_request.v2', 'decision_format': 'choice', 'enabled_predicates': [],
        'condition': 'The requested record is visible.', 'phase': 'after_action', 'action': None,
        'request_sha256': 'b' * 64, 'frames': [{'role': 'after', 'capture_id': 'synthetic-offline',
            'data_url': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')}]}
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        if request.url.path.endswith('/responses'):
            return httpx.Response(200, json=reply())
        return httpx.Response(200, json={'model': 'gpt-6-luna', 'answers': [{'type': 'choice', 'name': 'current_step',
            'choice': 'condition_not_met', 'confidence': 1.0, 'probabilities': [
                {'value': c, 'probability': float(c == 'condition_not_met')}
                for c in ('condition_met', 'condition_not_met', 'evidence_insufficient')]}],
            'usage': {'input_tokens': 20, 'output_tokens': 1, 'total_tokens': 21}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = OpenAIDecisionsProvider(api_key_env='CASCADE_TEST_KEY', client=client)
        provider.judge(deepcopy(payload))
        provider.review(deepcopy(payload))
    assert cascade.ordinary_body(calls[0]) == calls[1]


def test_http_clock_excludes_durable_response_writes(cascade, inputs, monkeypatch):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    clock = {'ns': 0}
    monkeypatch.setattr(cascade, 'perf_counter_ns', lambda: clock['ns'])
    monkeypatch.setattr(cascade, 'monotonic', lambda: clock['ns'] / 1_000_000_000)
    original_write = primary_runner._write
    def slow_disk(*a, **kw):
        value = original_write(*a, **kw)
        clock['ns'] += 20_000_000_000
        return value
    monkeypatch.setattr(primary_runner, '_write', slow_disk)
    def handle(request):
        clock['ns'] += 5_000_000
        return httpx.Response(200, json=reply())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert result['rows'][0]['status'] == 'completed'
    assert result['rows'][0]['ordinary']['http_elapsed_ms'] == 5.0


def test_reservation_without_dispatch_is_not_reissued(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    manifest = json.loads((args['output_dir'] / 'run.json').read_text(encoding='utf-8'))
    task = manifest['tasks'][0]
    budget = TokenBudget(args['output_dir'].parent / 'budget.jsonl')
    budget.reserve(task['review_request_id'], 1_000_000, task['review_request_sha256'])
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('reserved request resent'))) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    assert result['rows'][0]['status'] == 'unknown'
    assert result['budget']['reserved'] == 1_000_000


def test_late_response_retains_usage_without_adopting_choice(cascade, inputs, monkeypatch):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    clock = {'seconds': 0}
    monkeypatch.setattr(cascade, 'monotonic', lambda: clock['seconds'])
    def handle(request):
        clock['seconds'] += 12.001
        return httpx.Response(200, json=reply('condition_met'), headers={'x-request-id': 'late-offline'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)
    row = result['rows'][0]
    assert row['status'] == 'error' and row['choice'] is None
    assert row['ordinary']['error'] == 'late_response' and row['ordinary']['provider_request_id'] == 'late-offline'
    assert result['budget']['spent'] == 53 and result['summary']['exact_correct'] == 0


def test_prepared_body_change_is_rejected_before_http(cascade, inputs):
    args = inputs({'rejected': 'condition_not_met'}, {'baseline': {}})
    cascade.prepare_cascade(**args)
    next((args['output_dir'] / 'requests').glob('*.body')).write_bytes(b'{}')
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('changed body sent'))) as client:
        with pytest.raises(ValueError, match='request_conflict'):
            cascade.execute_cascade(args['output_dir'], allow_execute=True, api_key_env='CASCADE_TEST_KEY', client=client)


def test_latency_sums_each_case_before_percentiles(cascade):
    rows = [{'case': 'a', 'category': 'graphic', 'group_id': 'g', 'variant': 'baseline',
        'expected': 'condition_not_met', 'choice': 'condition_not_met', 'status': 'completed',
        'http_elapsed_ms': 300, 'primary': {'status': 'completed', 'choice': 'condition_not_met',
            'http_elapsed_ms': 100, 'usage': {'input_tokens': 1, 'output_tokens': 0}},
        'ordinary': {'status': 'completed', 'http_elapsed_ms': 200, 'usage': {'input_tokens': 2, 'output_tokens': 1}},
        'primary_false_pass': False},
        {'case': 'b', 'category': 'graphic', 'group_id': 'g', 'variant': 'baseline',
        'expected': 'condition_met', 'choice': 'condition_met', 'status': 'completed',
        'http_elapsed_ms': 500, 'primary': {'status': 'completed', 'choice': 'condition_not_met',
            'http_elapsed_ms': 400, 'usage': {'input_tokens': 1, 'output_tokens': 0}},
        'ordinary': {'status': 'completed', 'http_elapsed_ms': 100, 'usage': {'input_tokens': 2, 'output_tokens': 1}},
        'primary_false_pass': False}]
    for row in rows:
        row['primary'].update({key: row[key] for key in ('case', 'category', 'group_id', 'expected')})
    result = cascade.summarize_cascade(rows)
    assert result['http_latency'] == {'n': 2, 'p50_ms': 400, 'p95_ms': 500}
    assert result['input_tokens'] == 6 and result['output_tokens'] == 2
