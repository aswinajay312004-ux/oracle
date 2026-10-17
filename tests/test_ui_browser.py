"""The flowchart (frontend/js/graph.js) driven in a real browser: drag versus click, a press released outside
the diagram, keyboard focus after ＋, the zoom readout, and the diagram toolbar never covering app chrome.
Then a smoke run of the real app's screens under the strict Content-Security-Policy.

A tiny static server serves the real frontend files with the app's Content-Security-Policy, plus a harness
page shaped like Ask AI (an answer with a diagram, then the sticky composer). The smoke run starts
tests/demo_server.py (the app on a stand-in Oracle). Every page fails its test on a CSP violation or a console
error. Skipped when Playwright or a Chromium for it is not installed (set POPPYS_CHROMIUM to a chrome
executable to use a specific one); with POPPYS_REQUIRE_BROWSER=1 (CI's browser job) a missing browser fails
instead, so the job cannot pass by skipping everything."""
import glob
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

REQUIRED = os.environ.get('POPPYS_REQUIRE_BROWSER') == '1'
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip('playwright.sync_api')


def unavailable(message):
    (pytest.fail if REQUIRED else pytest.skip)(message)

TESTS = Path(__file__).resolve().parent
FRONTEND = TESTS.parent / 'frontend'
# the app's policy (backend/security.py): no inline script or style ('unsafe-inline' nowhere), no eval,
# nothing from other sites
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml',
         '.png': 'image/png', '.woff2': 'font/woff2', '.json': 'application/json'}
HARNESS = b'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Flowchart harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/harness.css"><script type="module" src="/harness.js"></script></head>
<body class="harness"><main>
  <div class="thread"><div class="above"></div><div class="flow-wrap" id="g"></div><div class="below"></div></div>
  <div class="composer"><form id="chat-form"><textarea rows="1" aria-label="Question"></textarea>
    <button class="primary" type="submit" id="ask">Ask</button></form></div>
</main></body></html>'''
HARNESS_CSS = b'''body.harness { margin: 0; } body.harness > main { width: 1100px; padding: 16px; }
.harness .above { height: 900px; } .harness .below { height: 1400px; }'''
HARNESS_JS = b'''import { renderGraph } from '/static/js/graph.js';
document.addEventListener('submit', e => { e.preventDefault(); window.asked = (window.asked || 0) + 1; });
window.renderGraph = renderGraph; window.harnessReady = true;'''
# Every blocked inline style (or script) fires securitypolicyviolation; collected for the assertions below.
WATCH_CSP = '''window.__csp = [];
document.addEventListener('securitypolicyviolation', e => window.__csp.push(
  `${e.effectiveDirective} blocked ${e.blockedURI || 'inline'} at ${e.sourceFile || ''}:${e.lineNumber}`), true);'''
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
        elif path == '/harness.css':
            body, kind = HARNESS_CSS, 'text/css'
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
        unavailable(f'Playwright cannot start: {exc}')
    found = None
    for path in chromium_paths():
        try:
            found = pw.chromium.launch(**({'executable_path': path} if path else {}))
            break
        except Exception:
            continue
    if not found:
        pw.stop()
        unavailable('No Chromium for Playwright (run "playwright install chromium" or set POPPYS_CHROMIUM)')
    yield found
    found.close(); pw.stop()


@pytest.fixture(scope='module')
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown(); server.server_close()


def watched(browser, **kw):
    """A page whose console errors, page errors and CSP violations are collected in page.problems."""
    pg = browser.new_page(**kw)
    pg.problems = []
    pg.add_init_script(WATCH_CSP)
    pg.on('console', lambda m: m.type == 'error' and pg.problems.append(m.text))
    pg.on('pageerror', lambda e: pg.problems.append(str(e)))
    return pg


def csp_violations(pg):
    return pg.evaluate('() => window.__csp || []')


@pytest.fixture
def page(browser, site):
    pg = watched(browser, viewport={'width': 1200, 'height': 700})
    pg.goto(site + '/')
    for _ in range(100):   # CSP blocks string-built wait_for_function, so poll instead
        if pg.evaluate('() => window.harnessReady === true'):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f'harness did not load: {pg.problems}')
    yield pg
    violations = csp_violations(pg)
    pg.close()
    assert not violations, violations
    assert not pg.problems, pg.problems


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


def test_drag_still_pans_when_another_field_had_focus(page):
    """In the app the question box usually has focus: pressing on the diagram moves focus away, and that
    element blur used to reach the window-level 'blur' watcher and cancel every drag at once."""
    render(page)
    page.locator('.composer textarea').focus()
    before = page.evaluate(VIEW)
    x, y = centre(node_box(page, 'B'))
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 60, y + 10, steps=8); page.mouse.up()
    page.wait_for_timeout(50)
    assert page.evaluate(VIEW)[0] > before[0] + 30          # the picture followed the drag
    assert page.evaluate('() => picked') == []              # and no table preview opened


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


# ---- the real app under the strict policy ------------------------------------------------------------------------
def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def app_site(tmp_path_factory):
    """tests/demo_server.py (the app on a stand-in Oracle with SQLite demo data) in its own process."""
    port, log = free_port(), tmp_path_factory.mktemp('demo-server') / 'server.log'
    with log.open('w') as out:
        proc = subprocess.Popen([sys.executable, str(TESTS / 'demo_server.py'), str(port)], cwd=TESTS.parent,
                                env=dict(os.environ, POPPYS_INLINE_PARSE='1'), stdout=out, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(600):
            if proc.poll() is not None:
                pytest.fail(f'the demo server exited: {log.read_text()[-2000:]}')
            try:
                urllib.request.urlopen(base + '/api/session', timeout=2).read()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail('the demo server did not start')
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()


def until(check, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError('timed out')


def style(pg, selector, prop):
    return pg.evaluate('([s, p]) => getComputedStyle(document.querySelector(s)).getPropertyValue(p)', [selector, prop])


def test_app_screens_under_the_strict_policy(browser, app_site, tmp_path):
    """Set-up, upload, reading the file, review, Ask AI with its flowchart, Database and Setup under style-src 'self':
    no CSP violation, no console error, and looks that used to be inline styles still apply (classes now, or values
    set through the CSSOM)."""
    from fixtures import merch_bom
    pg = watched(browser, viewport={'width': 1440, 'height': 900})
    pg.set_default_timeout(60000)
    # the page runs under exactly this policy, whatever the server sends
    pg.route(app_site + '/', lambda route: route.fulfill(response=(r := route.fetch()), headers={**r.headers, 'content-security-policy': CSP}))
    try:
        pg.goto(app_site + '/'); pg.wait_for_selector('#auth-submit')
        until(lambda: 'administrator' in pg.inner_text('#auth-submit').lower())   # a new workspace: create the administrator
        assert style(pg, '.auth-art .brand-text strong', 'font-size') == '18px'
        pg.fill('#auth-user', 'admin'); pg.fill('#auth-pass', 'smoke-test-password'); pg.click('#auth-submit')
        pg.wait_for_selector('#auth', state='hidden')
        assert style(pg, '#stage-upload > section.card', 'margin-top') == '16px'

        f = tmp_path / 'merch_bom.xlsx'; f.write_bytes(merch_bom())
        pg.set_input_files('#file-input', str(f)); pg.wait_for_selector('#go-review', state='visible')
        delays = pg.evaluate("() => [...document.querySelectorAll('#read-steps li')].map(li => getComputedStyle(li).animationDelay)")
        assert delays[:3] == ['0s', '0.08s', '0.16s'], delays            # the steps still rise in one after another
        assert style(pg, '#read-actions', 'justify-content') == 'flex-end'
        pg.click('#go-review'); pg.wait_for_selector('#stage-review .matrix')
        assert style(pg, '#stage-review .matrix thead .req', 'color') == 'rgb(212, 42, 42)'   # the red * of a required column
        assert style(pg, '#stage-review .screen', 'margin-bottom') == '14px'
        pg.click('[data-tab="fabric"]'); pg.wait_for_selector('.fabric-table')
        assert style(pg, '.fabric-table input.w-76', 'width') == '76px'

        pg.click('.nav [data-view="chat"]'); pg.wait_for_selector('#welcome')
        assert style(pg, '#welcome .welcome-lead', 'max-width') == '560px'
        pg.fill('#chat-input', 'Size wise quantity for order PK/26-27/S/9001'); pg.keyboard.press('Enter')
        pg.wait_for_selector('#thread .answer .gsvg')
        assert style(pg, '#thread .legend i.sw-brand', 'background-color') == 'rgb(15, 107, 98)'
        until(lambda: pg.evaluate("() => document.querySelector('#thread .gsvg').style.height.endsWith('px')"))   # sized through the CSSOM

        pg.click('.nav [data-view="explorer"]'); pg.wait_for_selector('#table-view tr[data-row]')
        assert style(pg, '#table-view .ex-head > div', 'flex-grow') == '1'
        pg.click('[data-xtab="links"]'); pg.wait_for_selector('#table-view .gsvg')
        assert style(pg, '#table-view .flow-wrap', 'min-height') == '240px'

        pg.click('.nav [data-view="admin"]'); pg.wait_for_selector('#admin-body .card')
        pg.click('[data-admin="dictionary"]'); pg.wait_for_selector('#dict-list .tl-item')
        assert style(pg, '#dict-list', 'max-height') == '560px' and style(pg, '#rel-filter', 'width') == '220px'
        assert csp_violations(pg) == []
        assert pg.problems == []
    finally:
        pg.close()
