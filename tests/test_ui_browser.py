"""The flowchart (frontend/js/graph.js) driven in a real browser: drag versus click, a press released outside
the diagram, keyboard focus after ＋, the zoom readout, and the diagram toolbar never covering app chrome.

A tiny static server serves the real frontend files with the app's Content-Security-Policy, plus a harness
page shaped like Ask AI (an answer with a diagram, then the sticky composer). Skipped when Playwright or a
Chromium for it is not installed (set POPPYS_CHROMIUM to a chrome executable to use a specific one)."""
import glob
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

sync_api = pytest.importorskip('playwright.sync_api')

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'
# the app's policy (backend/main.py): no inline script, no eval, nothing from other sites
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'")
TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml',
         '.png': 'image/png', '.woff2': 'font/woff2', '.json': 'application/json'}
HARNESS = b'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Flowchart harness</title>
<link rel="stylesheet" href="/static/styles.css"><script type="module" src="/harness.js"></script></head>
<body style="margin:0"><main style="width:1100px;padding:16px">
  <div class="thread"><div style="height:900px"></div><div class="flow-wrap" id="g"></div><div style="height:1400px"></div></div>
  <div class="composer"><form id="chat-form"><textarea rows="1" aria-label="Question"></textarea>
    <button class="primary" type="submit" id="ask">Ask</button></form></div>
</main></body></html>'''
HARNESS_JS = b'''import { renderGraph } from '/static/js/graph.js';
document.addEventListener('submit', e => { e.preventDefault(); window.asked = (window.asked || 0) + 1; });
window.renderGraph = renderGraph; window.harnessReady = true;'''
MODEL = {'nodes': [{'id': 'A', 'title': 'Order', 'code': 'ORDMAS', 'kind': 'header', 'base': True},
                   {'id': 'B', 'title': 'Order line', 'code': 'ORDSTYLE'},
                   {'id': 'C', 'title': 'Size quantity', 'code': 'ORDSIZE'}],
         'edges': [{'from': 'A', 'to': 'B', 'label': 'ORDMASID'}, {'from': 'B', 'to': 'C', 'label': 'SUBNO'}]}
VIEW = """() => { const m = (document.querySelector('#g .gcanvas').getAttribute('transform') || '')
  .match(/translate\\(([-\\d.]+) ([-\\d.]+)\\) scale\\(([-\\d.]+)\\)/); return m ? [+m[1], +m[2], +m[3]] : null; }"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/favicon.ico':
            self.send_response(204); self.end_headers(); return
        if path == '/':
            body, kind = HARNESS, 'text/html'
        elif path == '/harness.js':
            body, kind = HARNESS_JS, 'text/javascript'
        else:
            f = (FRONTEND / path.removeprefix('/static/')).resolve()
            if not path.startswith('/static/') or FRONTEND not in f.parents or not f.is_file():
                self.send_error(404); return
            body, kind = f.read_bytes(), TYPES.get(f.suffix, 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', CSP); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


def chromium_paths():
    if os.environ.get('POPPYS_CHROMIUM'):
        yield os.environ['POPPYS_CHROMIUM']
    yield None   # Playwright's own browser
    for root in filter(None, [os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), os.path.expanduser('~/.cache/ms-playwright')]):
        yield from sorted(glob.glob(os.path.join(root, 'chromium-*', 'chrome-linux*', 'chrome')), reverse=True)


@pytest.fixture(scope='module')
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:   # e.g. the Playwright driver is missing
        pytest.skip(f'Playwright cannot start: {exc}')
    found = None
    for path in chromium_paths():
        try:
            found = pw.chromium.launch(**({'executable_path': path} if path else {}))
            break
        except Exception:
            continue
    if not found:
        pw.stop()
        pytest.skip('No Chromium for Playwright (run "playwright install chromium" or set POPPYS_CHROMIUM)')
    yield found
    found.close(); pw.stop()


@pytest.fixture(scope='module')
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown(); server.server_close()


@pytest.fixture
def page(browser, site):
    pg = browser.new_page(viewport={'width': 1200, 'height': 700})
    problems = []
    pg.on('console', lambda m: m.type == 'error' and problems.append(m.text))
    pg.on('pageerror', lambda e: problems.append(str(e)))
    pg.goto(site + '/')
    for _ in range(100):   # CSP blocks string-built wait_for_function, so poll instead
        if pg.evaluate('() => window.harnessReady === true'):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f'harness did not load: {problems}')
    yield pg
    pg.close()
    assert not problems, problems


def render(page, expand=False):
    """Draw MODEL into #g; clicks are recorded in window.picked, ＋ (when expand) adds a table and redraws later."""
    page.evaluate("""([model, expand]) => {
      window.picked = []; const el = document.querySelector('#g'); const m = structuredClone(model);
      const draw = () => renderGraph(el, m, { onSelect: id => picked.push(id), onExpand: expand ? id => setTimeout(() => {
        m.nodes.push({ id: 'X' + m.nodes.length, title: 'Linked table' }); m.edges.push({ from: id, to: 'X' + (m.nodes.length - 1) }); draw(); }, 60) : undefined });
      draw(); el.scrollIntoView({ block: 'start' }); }""", [MODEL, expand])
    page.wait_for_timeout(150)   # the box is measured a frame after it is drawn


def centre(box):
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def node_box(page, node_id):
    return page.locator(f'#g .gnode[data-id="{node_id}"] rect.box').bounding_box()


def test_drag_moves_the_picture_and_never_counts_as_a_click(page):
    render(page)
    before = page.evaluate(VIEW)
    x, y = centre(node_box(page, 'B'))
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 60, y + 10, steps=8); page.mouse.up()
    page.wait_for_timeout(50)
    after = page.evaluate(VIEW)
    assert after[0] > before[0] + 30, (before, after)   # the picture followed the drag
    assert page.evaluate('() => picked') == []           # releasing on a table after a drag does not open it
    page.mouse.click(*centre(node_box(page, 'B')))
    assert page.evaluate('() => picked') == ['B']        # a plain click right after still works


def test_a_press_released_outside_the_diagram_does_not_leave_the_pointer_stuck(page):
    render(page)
    svg = page.locator('#g .gsvg').bounding_box()
    before = page.evaluate(VIEW)
    # press just inside the bottom edge and leave the diagram in one quick move, before a drag starts
    page.mouse.move(svg['x'] + 40, svg['y'] + svg['height'] - 2); page.mouse.down()
    page.mouse.move(svg['x'] + 40, svg['y'] + svg['height'] + 30); page.mouse.up()
    # then move over the diagram with no button pressed: the picture must stay put
    page.mouse.move(svg['x'] + 300, svg['y'] + 40, steps=10)
    assert page.evaluate(VIEW) == before
    page.mouse.click(*centre(node_box(page, 'A')))
    assert page.evaluate('() => picked') == ['A']        # and the next click on a table is not swallowed


def test_the_diagram_toolbar_stays_under_the_sticky_composer(page):
    render(page)
    tools, form = page.locator('#g .gtools').bounding_box(), page.locator('.composer form').bounding_box()
    # scroll so the toolbar sits behind the composer, as when reading an answer above the question box
    page.evaluate('d => window.scrollBy(0, d)', centre(tools)[1] - centre(form)[1])
    page.wait_for_timeout(100)
    tools, ask = page.locator('#g .gtools').bounding_box(), page.locator('#ask').bounding_box()
    assert abs(centre(tools)[1] - centre(page.locator('.composer form').bounding_box())[1]) < 30   # they overlap
    on_top = page.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('.composer, .gtools')?.className", list(centre(tools)))
    assert on_top == 'composer'
    before = page.evaluate(VIEW)
    page.mouse.click(*centre(ask))
    assert page.evaluate('() => window.asked') == 1      # the click reached Ask
    assert page.evaluate(VIEW) == before                 # and not the diagram's toolbar


def test_keyboard_focus_comes_back_to_the_same_plus_after_it_redraws(page):
    render(page, expand=True)
    page.focus('#g [data-plus="B"]')
    page.keyboard.press('Enter')
    page.wait_for_timeout(300)
    assert page.locator('#g .gnode').count() == 4         # the diagram was redrawn with the new table
    assert page.evaluate("() => document.activeElement.closest('[data-plus]')?.dataset.plus") == 'B'
    assert page.evaluate("() => document.activeElement.isConnected")


def test_the_zoom_readout_changes_once_per_zoom_and_never_during_a_drag(page):
    render(page)
    page.evaluate("""() => { window.changes = 0; window.said = [];
      new MutationObserver(m => { changes += m.length; }).observe(document.querySelector('#g [data-zl]'), { childList: true, characterData: true, subtree: true });
      new MutationObserver(() => said.push(document.querySelector('#g [data-zsay]').textContent)).observe(document.querySelector('#g [data-zsay]'), { childList: true, characterData: true, subtree: true }); }""")
    start = page.inner_text('#g [data-zl]')
    page.click('#g [data-z=in]'); page.wait_for_timeout(700)
    level = page.inner_text('#g [data-zl]')
    assert level != start and page.evaluate('() => changes') == 1   # the final value, not every animation frame
    assert page.evaluate('() => said') == [f'Zoom {level}']           # screen readers hear it once
    page.evaluate('() => { changes = 0; said.length = 0; }')
    svg = page.locator('#g .gsvg').bounding_box()
    page.mouse.move(svg['x'] + 30, svg['y'] + 30); page.mouse.down(); page.mouse.move(svg['x'] + 130, svg['y'] + 60, steps=20); page.mouse.up()
    page.wait_for_timeout(600)
    assert page.evaluate('() => [changes, said.length]') == [0, 0]
