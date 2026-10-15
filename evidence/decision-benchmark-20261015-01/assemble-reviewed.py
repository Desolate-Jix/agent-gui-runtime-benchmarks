"""Main逐张审阅后的冻结标注；只用已保留真实原图，不读取模型选择作标签。"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json

ROOT = Path('D:/AgentGUI-Projects/evidence/decision-benchmark-20261015-01')
IMAGE_KEYS = ('source_path', 'source_sha256', 'original_size', 'session_id', 'capture_id', 'group_id', 'cases')
CASE_KEYS = {'id', 'category', 'target', 'point', 'candidate_bbox', 'target_bbox', 'expected_text',
    'condition', 'expected', 'reason', 'appearance', 'container'}
ALIASES = {'wikipedia-home': 'wikipedia', 'python-documentation-3.14.7-home': 'python-docs',
    'pythonhome': 'python-docs', 'w3c-apg-combobox-family': 'w3c',
    'openlibrary': 'internetarchive', 'google-maps-place': 'google-maps'}
EXCLUDE = {'a06-graphic-bold-button': 'Interactive editor versus embedded illustration is not established by the static source.',
    'a06-graphic-bold-wrong-italic': 'Interactive editor versus embedded illustration is not established by the static source.'}
images, reviews = [], []
for name in ['annotations-a.json', 'annotations-b.json', 'annotations-c.json', 'annotations-d.json', 'annotations-main.json']:
    original = json.loads((ROOT/name).read_text(encoding='utf-8'))
    for im in original['images']:
        if not im.get('cases'):
            continue
        cleaned = {k: deepcopy(im.get(k)) for k in IMAGE_KEYS}
        cleaned['source_sha256'] = im.get('source_sha256', im.get('sha256'))
        cleaned['group_id'] = ALIASES.get(im['group_id'], im['group_id'])
        cleaned['cases'] = [{k: deepcopy(v) for k, v in c.items() if k in CASE_KEYS}
            for c in im['cases'] if c['id'] not in EXCLUDE]
        data = Path(cleaned['source_path']).read_bytes()
        assert sha256(data).hexdigest() == cleaned['source_sha256']
        images.append(cleaned)
        reviews.append({'annotation_file': name, 'source_path': cleaned['source_path'],
            'source_sha256': cleaned['source_sha256'], 'reviewer': 'Main AI', 'human_reviewed': False,
            'original_image_visually_inspected': True, 'accepted_case_ids': [c['id'] for c in cleaned['cases']],
            'excluded': {c['id']: EXCLUDE[c['id']] for c in im['cases'] if c['id'] in EXCLUDE}})


def simple(identifier, category, target, expected, reason, *, point=None, candidate_bbox=None,
           target_bbox=None, condition=None, appearance='', container=''):
    value = dict(id=identifier, category=category, target=target, expected=expected, reason=reason,
        point=point, candidate_bbox=candidate_bbox, target_bbox=target_bbox, appearance=appearance, container=container)
    if condition is not None:
        value['condition'] = condition
    return value


for batch, row_id, page_condition, page_expected in [
    ('01', 'action-review-9a5e1dc7d27ab135bae04d39d560d2e1a9752eb9',
        'The Google homepage is visible with its main search input.', 'condition_not_met'),
    ('02', 'action-review-62b2f33b55fb98a54cf106d38f99913b0c988923',
        'The Google homepage is visible with its main search input.', 'condition_met'),
    ('03', None, 'The Bing homepage with its main search input and background image is visible.', 'condition_met')]:
    path = Path('D:/AgentGUI-Projects/evidence/execution-speed-20261015-'+batch)
    rows = json.loads((path/'live-ledger-judgments.json').read_text(encoding='utf-8'))
    row = next(r for r in rows if r.get('request_id') == row_id) if row_id else rows[0]
    # 仅抽取来源及候选几何；模型 choice、probability、verdict 不用于标注。
    frame, action = row['frames'][0], row['action']
    candidate, target_check = action['candidate'], action['target_check']
    source = Path(frame['image_path'])
    assert sha256(source.read_bytes()).hexdigest() == frame['sha256']
    correct_box, point = candidate['bbox'], candidate['click_point']
    wrong_box = {'x': 75, 'y': 43, 'w': 2261, 'h': 34} if batch == '02' else {'x': 289, 'y': 8, 'w': 24, 'h': 25}
    wrong_point = {'x': 480, 'y': 60} if batch == '02' else {'x': 300, 'y': 20}
    cases = [
        simple('live'+batch+'-field-inside', 'input_field', target_check['target'], 'condition_met',
            'Main visually verified that the center is inside the original target field.',
            point=point, candidate_bbox=correct_box, target_bbox=correct_box,
            appearance=target_check['appearance'], container=target_check['container']),
        simple('live'+batch+'-field-other-control', 'input_field', target_check['target'], 'condition_not_met',
            'The point is on another browser control, outside the requested target field.',
            point=wrong_point, candidate_bbox=wrong_box, target_bbox=correct_box,
            appearance=target_check['appearance'], container=target_check['container']),
        simple('live'+batch+'-current-page', 'page_result', 'the current webpage', page_expected,
            'Main inspected the actual visible page independently of the address field and prior task outcome.', condition=page_condition)]
    from PIL import Image
    with Image.open(source) as image:
        size = list(image.size)
    im = dict(source_path=str(source), source_sha256=frame['sha256'], original_size=size,
        session_id=next(p.name for p in source.parents if p.name.startswith('session-')),
        capture_id=frame['capture_id'], group_id='edge-google-bing', cases=cases)
    images.append(im)
    reviews.append({'source_path': str(source), 'source_sha256': frame['sha256'], 'reviewer': 'Main AI',
        'human_reviewed': False, 'original_image_visually_inspected': True,
        'accepted_case_ids': [c['id'] for c in cases], 'known_failure_source_train_only': True})

for group, identifier, condition, reason in [
    ('pypi', 'pypi-install-not-observable', 'Installing requests has finished successfully on this computer.',
        'A package-index homepage does not show a local installation receipt.'),
    ('gnu', 'gnu-video-progress-not-observable', 'The visible video picture advanced during the last two seconds.',
        'One static capture does not establish a transition over two seconds.'),
    ('ubuntu', 'ubuntu-setting-persistence-not-observable', 'The tracker choice has persisted after a browser restart.',
        'A settings banner in one frame does not establish persisted state after a restart.'),
    ('nasa', 'nasa-video-progress-not-observable', 'The hero video picture advanced during the last two seconds.',
        'The static video frame and its controls do not establish temporal progress.'),
    ('internetarchive', 'library-carousel-progress-not-observable', 'The Trending Books carousel advanced during the last two seconds.',
        'The single carousel screenshot does not show a temporal transition.')]:
    im = next(i for i in images if i['group_id'] == group)
    item = simple(identifier, 'page_result', 'the currently supplied screenshot', 'evidence_insufficient',
        reason, condition=condition)
    im['cases'].append(item)
    next(r for r in reviews if r['source_sha256'] == im['source_sha256'])['accepted_case_ids'].append(identifier)

splits = {
    'httpbin-order-form': 'train', 'wikipedia': 'validation', 'select2-basic-usage': 'train',
    'w3c': 'test', 'quill-home-editor-demo': 'train', 'python-docs': 'train', 'google-maps': 'validation',
    'windows-calculator': 'train', 'windows-notepad': 'test', 'pypi': 'train', 'mozilla': 'validation',
    'libreoffice': 'test', 'gimp': 'train', 'audacity': 'validation', 'blender': 'test', 'rust': 'train',
    'go': 'validation', 'sqlite': 'test', 'postgresql': 'train', 'mysql': 'validation', 'debian': 'test',
    'ubuntu': 'train', 'archlinux': 'validation', 'kernel': 'test', 'gnu': 'train', 'w3schools': 'test',
    'npm': 'train', 'nasa': 'validation', 'internetarchive': 'test', 'edge-google-bing': 'train'}
assert {im['group_id'] for im in images} == set(splits)
all_cases = [c for im in images for c in im['cases']]
assert len({c['id'] for c in all_cases}) == len(all_cases)
for name, value in [
    ('annotations-reviewed.json', dict(schema='decision_benchmark_annotations.v1', suite_id='real-gui-20261015-v1',
        annotator='Sol AI initial annotation; Main AI visual review', images=images)),
    ('group-splits-reviewed.json', splits),
    ('main-visual-review.json', dict(schema='main_visual_review.v1', human_reviewed=False, reviews=reviews,
        notes=['Original screenshots visually inspected by Main before paid prompt comparison.',
            'Known live-failure sources are training only.', 'No synthetic UI or model answers used as truth.',
            'Derived markers and counterfactual wrong-control points are benchmark annotations, not actual executed clicks.']))]:
    target = ROOT/name
    assert not target.exists()
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(images=len(images), cases=len(all_cases), groups=len(splits),
    splits={s: sum(len(i['cases']) for i in images if splits[i['group_id']] == s) for s in ['train','validation','test']})))
