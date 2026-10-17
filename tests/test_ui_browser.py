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


# ---- the order flow in the real app: review findings -------------------------------------------------------------
# Each test drives tests/demo_server.py (fake Oracle) the way staff do: upload, review, check, approve, saved.
ADMIN_PW = 'smoke-test-password'
_runs = [int(time.time()) % 100000]


def sign_in(pg, base, user='admin', password=ADMIN_PW):
    """Open the app and sign in (creating the administrator on a new workspace)."""
    pg.goto(base + '/#workspace'); pg.wait_for_selector('#auth-submit', state='attached')
    until(lambda: pg.is_hidden('#auth') or any(w in pg.inner_text('#auth-submit').lower() for w in ('administrator', 'sign in')))
    if pg.is_visible('#auth'):
        pg.fill('#auth-user', user); pg.fill('#auth-pass', password); pg.click('#auth-submit')
        pg.wait_for_selector('#auth', state='hidden')
    until(lambda: pg.inner_text('#username') == user)


def unique_bom():
    """The merch BOM with one extra cell, so every run is a new workbook (the same file is never imported twice)."""
    from io import BytesIO
    from openpyxl import load_workbook
    from fixtures import merch_bom, save
    _runs[0] += 1
    wb = load_workbook(BytesIO(merch_bom())); wb.active['Z1'] = f'run {_runs[0]} {time.time()}'
    return save(wb)


def to_review(pg):
    pg.wait_for_selector('#dropzone')
    pg.set_input_files('#file-input', files=[{'name': f'bom_{_runs[0]}.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': unique_bom()}])
    pg.wait_for_selector('#go-review', state='visible'); pg.click('#go-review'); pg.wait_for_selector('#stage-review:not([hidden]) .matrix')


def fill_review(pg):
    for sel in ['[data-act="basis"][data-basis="order_demo"]', '[data-act="unit"][data-unit="PCS"]']:
        if pg.locator(sel).count():
            pg.click(sel)
    values = {'order_ref': f'PK/26-27/S/T{_runs[0]}', 'po_number': 'PO-77', 'order_date': '2026-10-01', 'ship_date': '2027-01-15', 'style': 'T1001'}
    for k, v in values.items():
        box = pg.locator(f'input[data-path="orders.0.{k}"]')
        if box.count() and (k == 'order_ref' or not box.input_value()):
            box.fill(v)


def to_approval(pg):
    to_review(pg); fill_review(pg)
    pg.click('#prepare'); pg.wait_for_selector('#stage-approve:not([hidden]) .kpis', timeout=60000)


def tick_notes(pg):
    if pg.locator('#ack-warnings').count():
        pg.check('#ack-warnings')


def press_accept(pg):
    tick_notes(pg)
    pg.click('[data-act2="approve"]'); pg.wait_for_selector('#confirm[open]')
    body = pg.inner_text('#confirm-body')
    pg.click('#confirm-ok')
    return body


def stage_shown(pg):
    return pg.evaluate("() => ['upload', 'read', 'review', 'approve', 'saved'].find(s => !document.querySelector('#stage-' + s).hidden)")


def kpi(pg, name):
    return pg.evaluate("n => [...document.querySelectorAll('#stage-approve .kpi')].find(k => k.querySelector('small').textContent === n).querySelector('strong').textContent", name)


def call(pg, path, method='GET', body=None):
    """A same-origin request from the page (its session cookie)."""
    return pg.evaluate("""async ([path, method, body]) => { const r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
      let d = null; try { d = await r.json(); } catch (_) { /* empty */ } return { status: r.status, data: d }; }""", [path, method, body])


def real_problems(pg):
    # refused requests the test causes on purpose are logged by Chromium as "Failed to load resource"
    return [p for p in pg.problems if 'Failed to load resource' not in p]


@pytest.fixture
def app_page(browser, app_site):
    pages = []

    def make(user='admin', password=ADMIN_PW):
        ctx = browser.new_context(viewport={'width': 1366, 'height': 860})
        pg = ctx.new_page(); pg.problems = []; pg.add_init_script(WATCH_CSP)
        pg.on('console', lambda m: m.type == 'error' and pg.problems.append(m.text))
        pg.on('pageerror', lambda e: pg.problems.append(str(e)))
        pg.set_default_timeout(30000)
        pg.route(app_site + '/', lambda route: route.fulfill(response=(r := route.fetch()), headers={**r.headers, 'content-security-policy': CSP}))
        if user:
            sign_in(pg, app_site, user, password)
        pages.append(pg)
        return pg
    yield make
    try:
        for pg in pages:
            assert csp_violations(pg) == [], csp_violations(pg)
            assert real_problems(pg) == [], real_problems(pg)
    finally:
        for pg in pages:
            pg.context.close()


def test_accept_never_saves_a_version_the_approver_did_not_see(app_page):
    """A colleague changes and re-checks the draft while it is on the approver's screen. Accept is refused (409);
    the approver must then see the changed draft and a plain reason, and only a fresh check can be saved."""
    a = app_page(); to_approval(a)
    seen = kpi(a, 'Pieces')
    draft_id = call(a, '/api/drafts')['data'][0]['id']
    b = app_page()
    d = call(b, f'/api/drafts/{draft_id}')['data']
    d['orders'][0]['lines'][0]['sizes'][0]['quantity'] = 999
    d = call(b, f'/api/drafts/{draft_id}', 'PUT', {'version': d['version'], 'orders': d['orders']})['data']
    assert call(b, f'/api/drafts/{draft_id}/preview', 'POST', {'version': d['version']})['status'] == 200
    press_accept(a)
    a.wait_for_selector('#review-notice')
    assert stage_shown(a) == 'review'
    notice = a.inner_text('#review-notice')
    assert 'Someone changed this draft — check it again' in notice and f'You checked {seen} pieces' in notice, notice
    assert a.input_value('input[data-path="orders.0.lines.0.sizes.0.quantity"]') == '999'   # the latest version is on screen
    assert call(a, f'/api/drafts/{draft_id}')['data'].get('result') is None                 # nothing was saved
    a.click('#prepare'); a.wait_for_selector('#stage-approve:not([hidden]) .kpis')
    pieces = kpi(a, 'Pieces')
    assert pieces != seen
    confirm = press_accept(a)
    assert f'Total: {pieces} pieces' in confirm, confirm            # the confirm box says how much is saved
    assert 'Not saved (mapping not verified yet)' in confirm         # and what is not (fabric and trims here)
    a.wait_for_selector('#stage-saved:not([hidden])')
    saved = call(a, f'/api/drafts/{draft_id}')['data']['result']
    assert saved['totals']['pieces'] == int(pieces.replace(',', ''))


def test_a_failed_accept_makes_the_check_stale_and_the_next_check_runs_preview(app_page):
    a = app_page(); to_approval(a)
    a.route('**/accept', lambda r: r.fulfill(status=409, content_type='application/json',
                                             body='{"detail": "Order reference PK/1 already exists. Existing orders are not overwritten."}'))
    press_accept(a)
    a.wait_for_selector('#accept-error')
    a.unroute('**/accept')
    bar = a.inner_text('#stage-approve .action-bar')
    assert 'already exists' in bar and 'Nothing was saved' in bar, bar          # a lasting message in the action bar
    assert a.locator('[data-act2="approve"]').count() == 0                       # the old check cannot be accepted again
    assert a.evaluate('() => document.activeElement.dataset.act2') == 'recheck'
    previews = []
    a.on('request', lambda r: r.url.endswith('/preview') and previews.append(r.url))
    a.click('[data-act2="back-review"]'); a.wait_for_selector('#stage-review:not([hidden])')
    assert a.inner_text('#prepare') == 'Check & continue →' and a.inner_text('#stage-review .doc-bar .pill') == 'Check it again'
    a.click('#prepare'); a.wait_for_selector('#stage-approve:not([hidden]) [data-act2="approve"]')
    assert len(previews) == 1
    # "Open the approval view" (a check that is still fresh) checks again too
    a.click('[data-act2="back-review"]'); a.wait_for_selector('#stage-review:not([hidden])')
    a.click('#prepare'); a.wait_for_selector('#stage-approve:not([hidden]) [data-act2="approve"]')
    until(lambda: len(previews) == 2)


def test_accept_with_no_answer_does_not_claim_nothing_was_saved(app_page):
    a = app_page(); to_approval(a)
    a.route('**/accept', lambda r: r.abort('connectionreset'))
    press_accept(a); a.wait_for_selector('#accept-error'); a.unroute('**/accept')
    text = a.inner_text('#accept-error')
    assert 'We could not confirm whether it was saved — open Saved orders to check' in text and 'Nothing was' not in text, text
    assert a.locator('#accept-error [data-goto-view="history"]').count() == 1
    # the answer is lost after the server saved it: the draft is read again and the saved screen shown
    a.click('[data-act2="recheck"]'); a.wait_for_selector('#stage-approve:not([hidden]) [data-act2="approve"]')

    def save_then_drop(route):
        route.fetch(); route.abort('connectionreset')
    a.route('**/accept', save_then_drop)
    press_accept(a); a.wait_for_selector('#stage-saved:not([hidden])'); a.unroute('**/accept')
    assert 'the order was saved' in ' '.join(a.locator('#toasts .toast span').all_inner_texts())


def test_saved_screen_says_verified_only_when_the_read_back_matched(app_page):
    a = app_page()
    held = []
    a.route('**/api/history/*/graph', lambda r: held.append(r))
    to_approval(a); press_accept(a); a.wait_for_selector('#stage-saved:not([hidden]) [data-hero="checking"]')
    assert a.inner_text('#stage-saved [data-eyebrow]').lower() == 'saved in oracle'          # not "verified" yet
    until(lambda: a.evaluate('() => true') and held)   # a Playwright call lets the route handler run
    held[0].fulfill(status=500, content_type='application/problem+json', body='{"detail": "Something went wrong. Nothing was saved. Reference abc123."}')
    a.wait_for_selector('#stage-saved [data-hero="unchecked"]')
    check = a.inner_text('#stage-saved [data-check]')
    assert check.startswith('Saved — the check after saving could not run:') and 'Nothing was saved' not in check, check
    assert 'Not saved:' in a.inner_text('#stage-saved .saved-hero')            # fabric/trims not saved, said at the top
    a.unroute('**/api/history/*/graph')

    def differ(route):
        resp = route.fetch(); g = resp.json(); g['verified'] = False
        n = next(x for x in g['nodes'] if x['table'] == 'ORDMAS'); n['verified'] = False
        n['records'][0]['matches'] = False; n['records'][0]['mismatched'] = ['DOCID']
        route.fulfill(response=resp, json=g)
    a.route('**/api/history/*/graph', differ)
    a.click('[data-act2="another"]'); to_approval(a); press_accept(a)
    a.wait_for_selector('#stage-saved [data-hero="differs"]')
    assert a.inner_text('#stage-saved [data-check]').startswith('Saved — differences found: Order Entry')
    a.unroute('**/api/history/*/graph')
    a.click('[data-act2="another"]'); to_approval(a); press_accept(a)
    a.wait_for_selector('#stage-saved [data-hero="verified"]')
    assert a.inner_text('#stage-saved [data-eyebrow]').lower() == 'saved and verified'


def test_warnings_must_be_ticked_before_accept(app_page):
    a = app_page(); to_approval(a)
    if not a.locator('#ack-warnings').count():
        pytest.skip('this plan has no warnings')
    assert a.is_disabled('[data-act2="approve"]')
    accepts = []
    a.on('request', lambda r: '/accept' in r.url and accepts.append(r.post_data_json))
    a.check('#ack-warnings'); assert a.is_enabled('[data-act2="approve"]')
    a.click('[data-act2="approve"]'); a.click('#confirm-ok'); a.wait_for_selector('#stage-saved:not([hidden])')
    assert accepts and accepts[0].get('acknowledged_warnings') is True


def test_sign_out_and_another_person_clear_the_draft_but_a_time_out_keeps_it(app_page, app_site):
    admin = app_page()
    call(admin, '/api/users', 'POST', {'username': 'priya', 'password': 'Garment-Floor-2026', 'role': 'operator'})
    call(admin, '/api/users/priya', 'PATCH', {'password': 'Garment-Floor-2026'})   # a temporary password again
    a = app_page()
    to_review(a); fill_review(a); a.fill('input[data-path="orders.0.season"]', 'KEEP ME')
    # the same person after a time-out: the corrections are still there and still save
    call(admin, '/api/sessions/sign-out-everyone', 'POST')
    a.click('#prepare'); a.wait_for_selector('#auth', state='visible')
    a.fill('#auth-user', 'admin'); a.fill('#auth-pass', ADMIN_PW); a.click('#auth-submit'); a.wait_for_selector('#auth', state='hidden')
    assert stage_shown(a) == 'review' and a.input_value('input[data-path="orders.0.season"]') == 'KEEP ME'
    assert a.locator('#toasts .toast').count() == 0                     # "Sign in to continue" is answered now
    # another person signs in after a time-out: nothing of the previous draft is left
    call(admin, '/api/sessions/sign-out-everyone', 'POST')
    a.click('#prepare'); a.wait_for_selector('#auth', state='visible')
    a.fill('#auth-user', 'priya'); a.fill('#auth-pass', 'Garment-Floor-2026'); a.click('#auth-submit')
    a.wait_for_selector('#pw-box:not([hidden])')                         # an administrator-set password: choose one first
    assert a.evaluate('() => document.activeElement.id') == 'pw-current'
    a.fill('#pw-current', 'Garment-Floor-2026'); a.fill('#pw-new', 'Floor-Own-Secret-77'); a.fill('#pw-repeat', 'Floor-Own-Secret-7'); a.click('#pw-submit')
    assert 'not the same' in a.inner_text('#pw-error')
    a.fill('#pw-repeat', 'Floor-Own-Secret-77'); a.click('#pw-submit'); a.wait_for_selector('#auth', state='hidden')
    assert a.inner_text('#username') == 'priya' and stage_shown(a) == 'upload'
    assert a.evaluate("() => ['review', 'approve', 'saved'].every(s => document.querySelector('#stage-' + s).innerHTML === '')")
    # signing out with unchecked corrections asks first, then clears everything
    to_review(a); a.fill('input[data-path="orders.0.season"]', 'PRIYA TYPED')
    a.click('#logout'); a.wait_for_selector('#confirm[open]'); a.click('#confirm-ok'); a.wait_for_selector('#auth', state='visible')
    assert a.evaluate("() => document.querySelector('#stage-review').innerHTML") == '' and stage_shown(a) == 'upload'
    # own password, any time: "Change my password" next to Sign out
    sign_in(a, app_site, 'priya', 'Floor-Own-Secret-77')
    a.click('#change-pw'); a.wait_for_selector('#modal[open] #cp-form')
    a.fill('#cp-current', 'wrong-password-123'); a.fill('#cp-new', 'Floor-Second-Secret-88'); a.fill('#cp-repeat', 'Floor-Second-Secret-88'); a.click('#cp-submit')
    until(lambda: a.is_visible('#cp-error'))
    a.fill('#cp-current', 'Floor-Own-Secret-77'); a.click('#cp-submit')
    until(lambda: not a.evaluate("() => document.querySelector('#modal').open"))


def test_keyboard_focus_stays_put_and_rows_and_drawer_work_by_keyboard(app_page):
    a = app_page(); to_review(a)
    assert a.evaluate('() => document.activeElement.id') == 'ws-title'          # a step change: its heading
    active = "() => { const e = document.activeElement; return e === document.body ? 'BODY' : (e.dataset.act || '') + ':' + (e.dataset.tab || e.dataset.unit || e.dataset.basis || e.dataset.li || ''); }"
    for sel, want in [('[data-act="basis"][data-basis="order_demo"]', 'basis:order_demo'), ('[data-act="unit"][data-unit="PCS"]', 'unit:PCS'),
                      ('[data-act="tab"][data-tab="trims"]', 'tab:trims')]:
        a.focus(sel); a.keyboard.press('Enter')
        assert a.evaluate(active) == want                                        # the same control after the redraw
    rows = a.locator('tr.group-row')
    assert rows.count() and all(t == 0 for t in a.evaluate("() => [...document.querySelectorAll('tr.group-row')].map(r => r.tabIndex)"))
    rows.first.focus(); a.keyboard.press('Enter')
    assert a.evaluate("() => document.activeElement.matches('tr.group-row') && document.activeElement.getAttribute('aria-expanded')") == 'true'
    a.click('[data-act="tab"][data-tab="order"]'); a.focus('[data-act="add-line"]'); a.keyboard.press('Enter')
    n = a.locator('[data-act="remove-line"]').count()
    a.focus(f'[data-act="remove-line"][data-li="{n - 1}"]'); a.keyboard.press('Enter')
    assert a.evaluate(active) == f'remove-line:{n - 2}'                           # removed: the row above
    a.click('.nav [data-view="explorer"]'); a.wait_for_selector('#table-view tr[data-row]')
    row = a.locator('#table-view tr[data-row]').first
    assert row.get_attribute('tabindex') == '0'
    row.focus(); a.keyboard.press('Enter'); a.wait_for_selector('#drawer:not([hidden])')
    assert a.evaluate('() => document.activeElement.id') == 'drawer-close'
    for key in ['Tab'] * 25 + ['Shift+Tab'] * 5:
        a.keyboard.press(key)
        assert a.evaluate("() => !!document.activeElement.closest('#drawer')"), key   # the modal drawer keeps focus
    a.keyboard.press('Escape')
    assert a.evaluate("() => document.activeElement.matches('#table-view tr[data-row]')")   # back on the row that opened it


def test_every_review_control_has_a_name(app_page):
    a = app_page(); to_review(a)
    unnamed = """() => [...document.querySelectorAll('#orders input, #orders select, #orders textarea')].filter(e => e.offsetParent !== null)
      .filter(e => !((e.labels && [...e.labels].some(l => l.textContent.trim())) || e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.title))
      .map(e => e.dataset.path || e.id || e.outerHTML.slice(0, 60))"""
    a.click('[data-act="basis"][data-basis="order_demo"]')
    assert a.evaluate(unnamed) == []
    a.click('[data-tab="fabric"]'); a.locator('[data-act="toggle-yarn"]').first.click()
    assert a.evaluate(unnamed) == []
    a.click('[data-tab="trims"]'); a.locator('tr.group-row').first.click()
    assert a.evaluate(unnamed) == []


def test_upload_errors_stay_on_the_drop_zone_in_plain_words(app_page):
    a = app_page()
    a.route('**/api/uploads', lambda r: r.fulfill(status=413, content_type='text/html', body='<html><body>413 Request Entity Too Large</body></html>'))
    a.set_input_files('#file-input', files=[{'name': 'big.xlsx', 'mimeType': 'application/octet-stream', 'buffer': b'x'}])
    a.wait_for_selector('#upload-error', state='visible'); a.unroute('**/api/uploads')
    assert 'The file is too large (the limit is 12 MB).' in a.inner_text('#upload-error')
    assert a.evaluate('() => document.activeElement.id') == 'dropzone'
    assert a.locator('#toasts .toast.error').count() == 1
    a.click('#upload-error [data-dismiss]')
    assert a.is_hidden('#upload-error') and a.evaluate('() => document.activeElement.id') == 'dropzone'


def test_browser_back_moves_between_screens_inside_the_app(app_page):
    a = app_page(); to_review(a); fill_review(a)
    a.click('#prepare'); a.wait_for_selector('#stage-approve:not([hidden]) .kpis')
    assert a.url.endswith('#workspace/approve')
    a.go_back(); a.wait_for_selector('#stage-review:not([hidden])')
    assert a.url.endswith('#workspace/review')
    a.go_forward(); a.wait_for_selector('#stage-approve:not([hidden])')
    a.click('.nav [data-view="history"]'); a.wait_for_selector('#view-history:not([hidden])')
    a.go_back(); a.wait_for_selector('#view-workspace:not([hidden]) #stage-approve:not([hidden])')
    a.go_back(); a.wait_for_selector('#stage-review:not([hidden])')
    a.fill('input[data-path="orders.0.season"]', 'NOT CHECKED')
    a.go_back(); a.wait_for_selector('#confirm[open]')                        # unchecked corrections: asked first
    a.click('#confirm button[value=cancel]')
    until(lambda: stage_shown(a) == 'review' and a.url.endswith('#workspace/review'))
    assert a.input_value('input[data-path="orders.0.season"]') == 'NOT CHECKED'


def test_csv_export_neutralises_formulas(page):
    with page.expect_download() as dl:
        page.evaluate("""async () => { const { csv } = await import('/static/js/util.js');
          csv([{ label: 'A', index: 0 }, { label: 'B', index: 1 }], [['=SUM(A1)', -5], ['+1', '@x'], ['-2', 'plain, text']]); }""")
    text = Path(dl.value.path()).read_bytes().decode('utf-8-sig')
    # text starting with = + - @ gets a leading ' (Excel shows it as text); numbers stay numbers
    assert text.split('\r\n') == ['A,B', "'=SUM(A1),-5", "'+1,'@x", "'-2,\"plain, text\""], text
