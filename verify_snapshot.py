"""逐文件校验私有备份，不调用模型或改写数据。"""
from hashlib import sha256
import json
from pathlib import Path

root = Path(__file__).resolve().parent
snapshot = json.loads((root / 'SNAPSHOT.json').read_text(encoding='utf-8'))
bad = []
for row in snapshot['files']:
    path = (root / row['path']).resolve()
    if (not path.is_relative_to(root) or not path.is_file()
            or path.stat().st_size != row['size']
            or sha256(path.read_bytes()).hexdigest() != row['sha256']):
        bad.append(row['path'])
if bad:
    raise SystemExit('Backup mismatch: ' + ', '.join(bad))
budget = root / 'evidence/decision-benchmark-20261015-01/api-token-budget.jsonl'
head = json.loads(budget.with_name(budget.name + '.lock').read_bytes().lstrip(b'\0').decode('utf-8'))
journal = budget.read_bytes()
if (head['schema'] != 'decision_benchmark_budget_head.v1' or len(journal) != head['bytes']
        or sha256(journal).hexdigest() != head['sha256']):
    raise SystemExit('Budget journal/checkpoint mismatch')
suite = json.loads((root / snapshot['suite_manifest']).read_text(encoding='utf-8'))
cases = suite['cases']
print(json.dumps({'passed': True, 'verified_files': len(snapshot['files']),
    'data_files': snapshot['data_files'], 'data_bytes': snapshot['data_bytes'],
    'cases': len(cases), 'images': len({c['source']['sha256'] for c in cases}),
    'families': len({c['group_id'] for c in cases})}))
