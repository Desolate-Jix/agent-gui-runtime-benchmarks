"""Main已查看三张原图后记录逐点真值；不读取模型答案。"""
from pathlib import Path
from hashlib import sha256
import json

ROOT = Path('D:/AgentGUI-Projects/evidence/decision-benchmark-collection-20261015-04')
OUT = Path('D:/AgentGUI-Projects/evidence/decision-benchmark-20261015-01/annotations-main.json')


def box(x, y, w, h):
    return dict(x=x, y=y, w=w, h=h)


def case(identifier, category, target, point, candidate, expected, reason, *, target_bbox=None,
         appearance='', container='', expected_text=None, condition=None):
    result = dict(id=identifier, category=category, target=target,
        point=dict(zip(('x', 'y'), point)) if point else None,
        candidate_bbox=candidate, target_bbox=target_bbox, expected=expected,
        reason=reason, appearance=appearance, container=container)
    if expected_text is not None:
        result['expected_text'] = expected_text
    if condition is not None:
        result['condition'] = condition
    return result


MET, NOT, UNKNOWN = 'condition_met', 'condition_not_met', 'evidence_insufficient'
address = box(76, 44, 961, 31)
pypi_field = box(320, 362, 611, 50)
images = []


def image(name, cases):
    receipt = json.loads((ROOT/(name+'-capture-1.json')).read_text(encoding='utf-8'))
    obs = receipt['observation']
    source = ROOT/'retained-sources'/(name+'-capture-1.png')
    assert sha256(source.read_bytes()).hexdigest() == obs['sha256']
    for item in cases:
        point, bbox = item['point'], item['candidate_bbox']
        if point:
            assert 0 <= point['x'] < obs['image_width'] and 0 <= point['y'] < obs['image_height']
            assert bbox and bbox['x'] <= point['x'] < bbox['x']+bbox['w'] and bbox['y'] <= point['y'] < bbox['y']+bbox['h']
    images.append(dict(source_path=str(source), source_sha256=obs['sha256'],
        original_size=obs['image_size'], session_id=Path(obs['image_path']).parent.parent.name,
        capture_id=None, group_id=name, original_image_path=obs['image_path'], cases=cases))


image('pypi', [
    case('pypi-search-hit', 'input_field', 'the project search input on the PyPI homepage', (530, 386), pypi_field, MET,
         'The point is inside the large white project search field.', target_bbox=pypi_field,
         appearance='A wide white rectangular search field with a magnifying-glass icon at the right.', container='Below the main PyPI heading.'),
    case('pypi-search-on-login', 'input_field', 'the project search input on the PyPI homepage', (994, 123), box(974, 110, 43, 28), NOT,
         'The point is on the Log in navigation link, not the project search field.', target_bbox=pypi_field,
         appearance='A wide white rectangular search field with a magnifying-glass icon at the right.', container='Below the main PyPI heading.'),
    case('pypi-search-icon', 'graphic', 'the magnifying-glass search icon inside the project search field', (904, 385), box(892, 373, 25, 27), MET,
         'The center is on the magnifying-glass icon.', target_bbox=box(892, 373, 25, 27), appearance='A gray magnifying-glass icon.', container='Right end of the central white search input.'),
    case('pypi-register-on-login', 'graphic', 'the Register navigation link on the PyPI page', (994, 123), box(974, 110, 43, 28), NOT,
         'The marked link reads Log in; Register is the separate neighboring link.', target_bbox=box(1045, 110, 62, 28), appearance='The white word Register.', container='Top blue navigation bar.'),
    case('pypi-address-value', 'text_value', 'the browser address bar', (300, 60), address, MET,
         'The complete displayed address is https://pypi.org.', expected_text='https://pypi.org'),
    case('pypi-placeholder-not-entered', 'text_value', 'the project search input on the PyPI homepage', (530, 386), pypi_field, NOT,
         'Only a search placeholder is visible, not the specified package query.', expected_text='requests'),
    case('pypi-homepage-result', 'page_result', 'the current PyPI webpage', None, None, MET,
         'The large package-index homepage heading and search input are visible.', condition='The PyPI homepage is visible with its project search input.'),
    case('pypi-not-project-detail', 'page_result', 'the current PyPI webpage', None, None, NOT,
         'This is the homepage, not a numpy package detail page.', condition='The numpy package detail page with its package-specific metadata is visible.'),
])

image('mozilla', [
    case('mozilla-address-hit', 'input_field', 'the browser address bar at the top of the window', (480, 60), address, MET,
         'The point lies inside the visible URL field.', target_bbox=address, appearance='A long rounded URL input.', container='Browser toolbar above the page.'),
    case('mozilla-address-on-products', 'input_field', 'the browser address bar at the top of the window', (559, 106), box(516, 91, 91, 31), NOT,
         'The point is on Products in the webpage navigation.', target_bbox=address, appearance='A long rounded URL input.', container='Browser toolbar above the page.'),
    case('mozilla-pause-button', 'graphic', 'the Pause animation button', (1153, 216), box(1085, 200, 137, 33), MET,
         'The point is on the explicitly labeled pause button.', target_bbox=box(1085, 200, 137, 33), appearance='A small dark outlined button with a pause icon and Pause animation text.', container='Upper-right of the black hero panel.'),
    case('mozilla-about-on-products', 'graphic', 'the About us navigation item', (559, 106), box(516, 91, 91, 31), NOT,
         'The point is on Products, while About us is to its right.', target_bbox=box(647, 91, 82, 31), appearance='Black text reading About us.', container='White page navigation header.'),
    case('mozilla-address-value', 'text_value', 'the browser address bar', (480, 60), address, MET,
         'The visible URL includes the en-US path and final slash.', expected_text='https://www.mozilla.org/en-US/'),
    case('mozilla-wrong-address-value', 'text_value', 'the browser address bar', (480, 60), address, NOT,
         'The URL is for Mozilla, not the stated PyPI address.', expected_text='https://pypi.org'),
    case('mozilla-homepage-result', 'page_result', 'the current webpage', None, None, MET,
         'The hero heading says Welcome to Mozilla.', condition='The Mozilla homepage with the Welcome to Mozilla heading is visible.'),
    case('mozilla-download-persistence', 'page_result', 'a completed local installer download', None, None, UNKNOWN,
         'This webpage screenshot has no download receipt or local file evidence.', condition='A local Firefox installer download has completed successfully.'),
])

image('libreoffice', [
    case('libreoffice-address-hit', 'input_field', 'the browser address bar', (480, 60), address, MET,
         'The point is within the URL text field.', target_bbox=address, appearance='A rounded URL text input.', container='Browser toolbar above the site.'),
    case('libreoffice-address-on-language', 'input_field', 'the browser address bar', (1060, 102), box(963, 88, 210, 27), NOT,
         'The point is on the site language selector below the browser toolbar.', target_bbox=address, appearance='A rounded URL text input.', container='Browser toolbar above the site.'),
    case('libreoffice-download-hit', 'graphic', 'the green DOWNLOAD NOW button', (488, 459), box(387, 436, 203, 48), MET,
         'The center is on the green DOWNLOAD NOW call-to-action.', target_bbox=box(387, 436, 203, 48), appearance='A green rounded button labeled DOWNLOAD NOW.', container='Below the main LibreOffice description.'),
    case('libreoffice-donate-on-download', 'graphic', 'the green DONATE button', (488, 459), box(387, 436, 203, 48), NOT,
         'The point is on DOWNLOAD NOW, not the separate DONATE button.', target_bbox=box(1033, 239, 113, 55), appearance='A green rounded DONATE button.', container='Page navigation row near the upper right.'),
    case('libreoffice-search-icon', 'graphic', 'the magnifying-glass website search icon', (932, 265), box(917, 248, 33, 35), MET,
         'The center is on the standalone gray magnifying-glass icon.', target_bbox=box(917, 248, 33, 35), appearance='A gray magnifying-glass icon.', container='Page navigation immediately left of DONATE.'),
    case('libreoffice-address-value', 'text_value', 'the browser address bar', (480, 60), address, MET,
         'The displayed address is the LibreOffice homepage.', expected_text='https://www.libreoffice.org'),
    case('libreoffice-wrong-address-value', 'text_value', 'the browser address bar', (480, 60), address, NOT,
         'The address is LibreOffice, not Microsoft.', expected_text='https://www.microsoft.com'),
    case('libreoffice-homepage-result', 'page_result', 'the current webpage', None, None, MET,
         'The LibreOffice branding and homepage download call-to-action are visible.', condition='The LibreOffice homepage with its DOWNLOAD NOW button is visible.'),
    case('libreoffice-embedded-editor-decoy', 'page_result', 'the foreground application', None, None, NOT,
         'The Writer UI is an illustration inside an Edge webpage, not a foreground native Writer window.', condition='The foreground native application is LibreOffice Writer with an editable document open.'),
])

assert not OUT.exists()
OUT.write_text(json.dumps(dict(schema='decision_benchmark_annotations.v1', suite_id='real-gui-20261015',
    annotator='Main AI visual review', images=images), ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(images=len(images), cases=sum(len(i['cases']) for i in images), output=str(OUT))))
