"""Flowchart view maths (frontend/js/graph.js): zoom limits, fit-to-view, keep-in-view panning and
zooming around the pointer. Runs the real ES module in Node; skipped where Node is not installed.
The pointer, keyboard and toolbar behaviour is checked in a browser (see docs / the UI review)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

GRAPH = Path(__file__).resolve().parents[1] / 'frontend' / 'js' / 'graph.js'
NODE = shutil.which('node')


def js(expr):
    """Evaluate expr (with the module imported as g) and return its JSON value."""
    code = f'const g = await import({json.dumps(GRAPH.as_uri())});\nprocess.stdout.write(JSON.stringify({expr}));'
    out = subprocess.run([NODE, '--input-type=module', '-e', code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(not NODE, reason='Node.js is not installed')


@needs_node
def test_zoom_limits_are_sensible():
    z = js('g.ZOOM')
    assert 0.1 <= z['min'] < 1 < z['max'] <= 4 and 1 < z['step'] <= 1.5
    assert js('typeof g.renderGraph') == 'function'


@needs_node
def test_fit_never_enlarges_and_shows_everything():
    assert js('g.fitZoom(2000, 400, 1000, 400)') == 0.5          # twice too wide → half size
    assert js('g.fitZoom(800, 1200, 1000, 300)') == 0.25         # too tall → limited by height
    assert js('g.fitZoom(100, 50, 1000, 400)') == 1              # small pictures stay at 100%


@needs_node
def test_keep_in_view():
    # a smaller picture can move but never leaves the box
    assert js('g.clampView({k: 1, x: -50, y: 900}, 300, 200, 1000, 400)') == {'k': 1, 'x': 0, 'y': 200}
    assert js('g.clampView({k: 1, x: 120, y: 30}, 300, 200, 1000, 400)') == {'k': 1, 'x': 120, 'y': 30}
    # a bigger picture never shows an empty edge
    assert js('g.clampView({k: 2, x: 50, y: -5000}, 1000, 400, 1000, 400)') == {'k': 2, 'x': 0, 'y': -400}
    assert js('g.clampView({k: 2, x: -500, y: -100}, 1000, 400, 1000, 400)') == {'k': 2, 'x': -500, 'y': -100}


@needs_node
def test_zoom_keeps_the_point_under_the_pointer():
    v = js('g.zoomAround({k: 1, x: 40, y: 10}, 2, 300, 120)')
    assert v['k'] == 2
    # the picture point under (300, 120) before and after is the same
    before = ((300 - 40) / 1, (120 - 10) / 1)
    after = ((300 - v['x']) / 2, (120 - v['y']) / 2)
    assert before == pytest.approx(after)
    assert js('g.zoomAround({k: 1.5, x: 0, y: 0}, 1.5, 10, 10)') == {'k': 1.5, 'x': 0, 'y': 0}


def test_graph_module_is_csp_safe():
    """script-src 'self': no inline handlers in generated markup, no eval, no external URLs."""
    src = GRAPH.read_text(encoding='utf-8')
    assert not re.search(r'\son[a-z]+\s*=\s*["\'`$]', src), 'inline event handler attribute in markup'
    assert 'eval(' not in src and 'new Function' not in src
    assert not re.search(r'https?://(?!www\.w3\.org)', src), 'external URL in graph.js'
    # every toolbar button is labelled for screen readers
    buttons = re.findall(r'<button[^>]*data-z="(\w+)"[^>]*>', src)
    assert sorted(buttons) == ['fit', 'in', 'out', 'reset']
    assert all('aria-label=' in b for b in re.findall(r'<button[^>]*data-z="\w+"[^>]*>', src))
