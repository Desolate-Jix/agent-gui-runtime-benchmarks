from pathlib import Path
from hashlib import sha256
import importlib.util
import json

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cascade_audit_runner', ROOT / 'run_cascade.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
p = ROOT / 'cascade-test-01'
run = json.loads((p / 'run.json').read_text(encoding='utf-8'))
c = run['config']
d = m._dependencies(c['source_root'])
frozen, primaries, bodies, all_rows = m._load_inputs(c['suite_path'], c['variants_path'],
    c['primary_run'], c['variant_names'], c['source_root'], d)
assert frozen == c
tasks = [m._task(row, bodies[row['request_id']], d) for row in primaries]
assert tasks == run['tasks']
rows = json.loads((p / 'results.json').read_text(encoding='utf-8'))
ordinary_count = 0
usage = 0
for task, primary, row in zip(tasks, primaries, rows, strict=True):
    assert row['primary'] == primary
    if task['review_request_id'] is None:
        assert row['choice'] == primary['choice'] == 'condition_met'
        assert row['ordinary']['attempted'] is False
        continue
    request_id = task['review_request_id']
    raw_request = (p / 'requests' / (request_id + '.body')).read_bytes()
    assert sha256(raw_request).hexdigest() == task['review_request_sha256']
    assert json.loads(raw_request) == m.ordinary_body(bodies[primary['request_id']])
    ordinary = m._read_ordinary(task, p, d)
    assert ordinary == row['ordinary']
    assert ordinary['status'] == 'completed' and ordinary['error'] is None
    assert row['choice'] == ordinary['choice']
    assert row['http_elapsed_ms'] == primary['http_elapsed_ms'] + ordinary['http_elapsed_ms']
    usage += ordinary['usage']['total_tokens']
    ordinary_count += 1
assert ordinary_count == 164 and usage == 224804
summary = m.summarize_cascade(rows, source_root=c['source_root'])
assert summary == json.loads((p / 'summary.json').read_text(encoding='utf-8'))
budget = d.budget.TokenBudget(Path(c['budget_path'])).snapshot()
assert budget['spent'] == 1431435 and budget['reserved'] == 0
errors = [{key: row[key] for key in ('case', 'variant', 'repeat', 'expected', 'choice', 'category')}
    | {'primary_choice': row['primary']['choice'], 'ordinary_choice': row['ordinary'].get('choice')}
    for row in rows if row['choice'] != row['expected']]
report = {'all_primary_bindings_verified': len(primaries), 'all_secondary_raw_and_usage_verified': ordinary_count,
    'secondary_tokens': usage, 'budget': {key: budget[key] for key in ('limit', 'spent', 'reserved', 'remaining')},
    'errors': errors, 'by_variant': summary['by_variant'], 'files_sha256': {
        name: sha256((p / name).read_bytes()).hexdigest() for name in ('run.json', 'results.json', 'summary.json')}}
(ROOT / 'cascade-audit-main.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: report[key] for key in ('all_primary_bindings_verified', 'all_secondary_raw_and_usage_verified',
    'secondary_tokens', 'budget')}, ensure_ascii=False))
