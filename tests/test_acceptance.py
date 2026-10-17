"""Acceptance tests A–F of docs/EXCEL_UNDERSTANDING.md §8, black-box through the HTTP API.

CI runs everything against the in-memory Oracle stand-in (tests/fake_oracle.py) and, for F, the SQLite
copy of the GARMENT_DEMO structure (tests/demo_db.py). The opt-in variants run E and F on the local
replica:  POPPYS_TEST_ORACLE=1 .venv/bin/python -m pytest -q tests/test_acceptance.py
Every assertion message names the spec rule it checks, so a failure reads as the missing requirement.

API shapes the spec leaves open and these tests assume:
* POST /api/drafts/{id}/map carries the unit decision as the order fields qty_unit, pieces_per_unit and
  quantity_basis next to definition / save_template / template_name.
* matcher.check(sheet, definition, fields) takes the sheet of the cell model (draft.sheets[i]) and the
  effective field list exactly as GET /api/fields returns it.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import os
import re
import secrets
import pytest
from fastapi.testclient import TestClient
from backend import ai, local_store, main, oracle_store
from backend.config import OWNER
from backend.erp import mapping as registry
from backend.insight import service
from backend.insight.query import Dialect
from fake_oracle import FakeOracle
from fixtures import (zen_kids_po, blue_bay_po, flat_qty_orders, wrong_mapping_po, merch_bom,
                      as_xls, as_csv, as_xml2003, as_html, as_json_records)
import demo_db

PW = 'test-password-123'
REAL = pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE') != '1', reason='Set POPPYS_TEST_ORACLE=1 to run E/F on the local Oracle replica')


# --- environment ----------------------------------------------------------------------------------
def offline_ai(monkeypatch):
    """Ollama may be offline: every test runs without it unless it installs its own fake."""
    def down(*a, **k):
        raise RuntimeError('local AI is offline in tests')
    monkeypatch.setattr(ai, '_chat', down)
    monkeypatch.setattr(ai, 'available', lambda: False)
    monkeypatch.setattr(ai, 'status', lambda: dict(available=False, models=[], model='test'))


def signed_in(client):
    assert client.post('/api/setup', json={'username': 'admin', 'password': PW}).status_code == 200
    return client


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path); monkeypatch.setattr(main, 'DATA', tmp_path)
    offline_ai(monkeypatch)
    oracle = FakeOracle(masters={'BUYMAS': [dict(id=7, label='TESTBUYER', cancelled=False)],
                                 'UNITMAS': [dict(id=8, label='PCS', cancelled=False)]}).install(monkeypatch)
    with TestClient(main.app) as c:
        yield signed_in(c), oracle


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Stand-in Oracle for saving + the SQLite GARMENT_DEMO copy for the Database screen; every approved
    row is copied into that copy, as tests/demo_server.py does, so the explorer reads what was saved."""
    for mod in (local_store, main, service):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    service.invalidate(); main.clear_counts(); offline_ai(monkeypatch)
    conn = demo_db.create()
    masters = {m['table']: [dict(id=k, label=v, cancelled=x == 'T') for k, v, x in conn.execute(f'SELECT {m["key"]},{m["label"]},CANCEL FROM {m["table"]}')]
               for m in registry.effective()['masters'].values() if m['table'] in demo_db.columns()}
    FakeOracle(masters=masters).install(monkeypatch)
    commit = oracle_store.commit

    def commit_and_copy(draft, expected_hash, approver, mapping=None):
        result = commit(draft, expected_hash, approver, mapping)
        if not result.get('already_committed'):
            for row in result['rows']:
                conn.execute(f'INSERT INTO {row["table"]} ({",".join(row["values"])}) VALUES ({",".join("?" * len(row["values"]))})',
                             list(row['values'].values()))
            conn.commit()
        return result
    monkeypatch.setattr(oracle_store, 'commit', commit_and_copy)
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); dialect = Dialect('sqlite')

    @contextmanager
    def sources():
        yield {'db': (ex, dialect), 'local': (ex, dialect)}
    monkeypatch.setattr(main, 'knowledge_base', lambda: kb)
    monkeypatch.setattr(main, 'query_sources', sources)
    with TestClient(main.app) as c:
        yield signed_in(c)


@pytest.fixture
def real(tmp_path, monkeypatch):
    """The real app against the local Oracle replica; only the local workspace (SQLite) is temporary."""
    for mod in (local_store, main, service):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    service.invalidate(); main.clear_counts(); offline_ai(monkeypatch)
    with TestClient(main.app) as c:
        yield signed_in(c)


# --- helpers ------------------------------------------------------------------------------------------
def upload(c, content, name):
    r = c.post('/api/uploads', files={'file': (name, content)})
    assert r.status_code == 200, f'upload of {name} failed: {r.status_code} {r.text[:400]}'
    return r.json()


def edit(c, d, **fields):
    o = deepcopy(d['orders'][0]); o.update(fields)
    r = c.put(f'/api/drafts/{d["id"]}', json={'version': d['version'], 'orders': [o]})
    assert r.status_code == 200, r.text
    return r.json()


def preview(c, d):
    r = c.post(f'/api/drafts/{d["id"]}/preview', json={'version': d['version']})
    assert r.status_code == 200, r.text
    return r.json()


def accept(c, d):
    return c.post(f'/api/drafts/{d["id"]}/accept', json={'version': d['version'], 'plan_hash': d['plan']['hash']})


def map_layout(c, d, definition, **body):
    """The person's one-time confirmation of a layout (plus, optionally, the unit decision)."""
    return c.post(f'/api/drafts/{d["id"]}/map', json=dict(version=d['version'], sheet=d['sheets'][0]['name'], definition=definition, **body))


def errors(plan):
    """{path: [messages]} of blocking issues: exactly the fields the review screen outlines red."""
    out = {}
    for i in plan.get('issues', []):
        if i['severity'] == 'error':
            out.setdefault(i.get('path'), []).append(i['message'])
    return out


def strings(value, skip=()):
    """Every text leaf of a JSON value, leaving out keys that hold explanations rather than data."""
    if isinstance(value, dict):
        return [s for k, v in value.items() if k not in skip for s in strings(v, skip)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v, skip)]
    return [value] if isinstance(value, str) else []


def norm(v):
    return re.sub(r'[^a-z0-9]+', ' ', str(v or '').lower()).strip()


def rc(ref):
    m = re.fullmatch(r'([A-Z]+)(\d+)', str(ref or ''))
    assert m, f'"{ref}" is not a cell address'
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


def value_at(sheet, ref):
    return next((c['value'] for c in sheet['cells'] if c['cell'] == ref), None)


def unit(o):
    return o.get('qty_unit'), o.get('pieces_per_unit'), o.get('quantity_basis')


# --- the ZEN KIDS buyer layout (A, E, F) ---------------------------------------------------------------
ZEN_FIELDS = {'buyer': 'B4', 'style': 'B5', 'description': 'B6', 'po_number': 'B7', 'ship_date': 'B8', 'season': 'B9'}
ZEN_LABELS = {'buyer': 'A4', 'style': 'A5', 'description': 'A6', 'po_number': 'A7', 'ship_date': 'A8', 'season': 'A9'}
ZEN_DEFINITION = {'fields': ZEN_FIELDS, 'table': {'type': 'matrix', 'header_row': 11, 'color_col': 1,
                                                  'first_size_col': 3, 'last_size_col': 6, 'total_col': 7}}
PACKS_OF_3 = {'qty_unit': 'PACK', 'pieces_per_unit': 3, 'quantity_basis': 'sample'}
SECOND_ZEN = dict(style='ZK-2002', description='BABY SLEEPSUIT', po='ZK/PO/7790', ship=datetime(2027, 3, 1), season='AW-27',
                  lines=(('WHITE', '', (12, 24, 36, 48)), ('PINK', '', (6, 6, 6, 6)), ('LEMON', 'new colour', (1, 2, 3, 4))))
SECOND_LINES = [('WHITE', [('S', 12), ('M', 24), ('L', 36), ('XL', 48)]), ('PINK', [('S', 6), ('M', 6), ('L', 6), ('XL', 6)]),
                ('LEMON', [('S', 1), ('M', 2), ('L', 3), ('XL', 4)])]
SECOND_PIECES = 3 * (120 + 24 + 10)   # packs of 3
FIRST_ZEN_VALUES = ('ZK-1001', 'BABY ROMPER', 'ZK/PO/7781', '2027-01-15', 'SS-27', 'NAVY', 'GREY', 'repeat style')
FORMATS = {'xlsx': (lambda b: b, 'zen-2.xlsx'), 'xls': (as_xls, 'zen-2.xls'), 'csv': (as_csv, 'zen-2.csv'),
           'xml2003': (as_xml2003, 'zen-2.xml'), 'html-export': (as_html, 'zen-2-export.xls')}


def zen_template(c, name='ZEN KIDS PO', content=None, **unit_choice):
    d = upload(c, content or zen_kids_po(), 'zen-1.xlsx')
    r = map_layout(c, d, ZEN_DEFINITION, save_template=True, template_name=name, **(unit_choice or PACKS_OF_3))
    assert r.status_code == 200, f'§6: mapping a new layout once and saving it as a buyer template must succeed: {r.text[:400]}'
    return r.json()


def approved_zen_order(c, ref, content=None):
    """Upload → map the layout once (packs of 3) → fill what the file lacks → preview → approve."""
    d = upload(c, content or zen_kids_po(**SECOND_ZEN), 'zen-po.xlsx')
    r = map_layout(c, d, ZEN_DEFINITION, **PACKS_OF_3)
    assert r.status_code == 200, r.text
    p = preview(c, edit(c, r.json(), order_ref=ref, order_date='2026-10-17', **PACKS_OF_3))
    assert p['plan']['live'] and not p['plan']['errors'], f'E: a fully reviewed order previews without questions: {p["plan"]["errors"]}'
    assert p['plan']['totals']['pieces'] == SECOND_PIECES, f'§5: packs of 3 are saved as pieces (×3): {p["plan"]["totals"]}'
    r = accept(c, p)
    assert r.status_code == 200, f'E: approving a reviewed order saves it: {r.text[:400]}'
    return p, r.json()


# --- A: known buyer ------------------------------------------------------------------------------------
@pytest.mark.parametrize('fmt', list(FORMATS))
def test_A_known_buyer_next_file_is_read_with_the_saved_layout_and_unit(env, fmt):
    c, _ = env
    first = zen_template(c)
    assert unit(first['orders'][0]) == ('PACK', 3, 'sample'), \
        f'§5/§6: the unit chosen while mapping (packs of 3, sample quantities) must be on the mapped draft: {unit(first["orders"][0])}'
    [t] = c.get('/api/templates').json()
    assert t.get('buyer') == 'ZEN KIDS' and t.get('version') == 1 and t['active'], \
        f'§6: a saved template records the buyer and starts at version 1: { {k: t.get(k) for k in ("buyer", "version", "active")} }'
    assert 'PACK' in json.dumps(t.get('unit')).upper() and '3' in json.dumps(t.get('unit')) and t.get('quantity_basis') == 'sample', \
        f'§6: the template stores the unit decision (packs of 3) and quantity basis: unit={t.get("unit")} basis={t.get("quantity_basis")}'
    anchors = t.get('anchors') or {}
    got = {k: ((anchors.get(k) or {}).get('cell'), (anchors.get(k) or {}).get('label_cell'), norm((anchors.get(k) or {}).get('label'))) for k in ZEN_FIELDS}
    want = {k: (ZEN_FIELDS[k], ZEN_LABELS[k], norm(value_at(first['sheets'][0], ZEN_LABELS[k]))) for k in ZEN_FIELDS}
    assert got == want, f'§6: anchors[field] = {{cell, label_cell, label}} for every mapped field: {got}'

    convert, name = FORMATS[fmt]
    d = upload(c, convert(zen_kids_po(**SECOND_ZEN)), name)
    assert 'ZEN KIDS PO' in d['format'] and len(d['orders']) == 1, \
        f'A: the next ZEN KIDS file (different values, {fmt}) must be read automatically with the saved template; got format "{d["format"]}"'
    o = d['orders'][0]
    assert {k: o.get(k) for k in ZEN_FIELDS} == dict(buyer='ZEN KIDS', style='ZK-2002', description='BABY SLEEPSUIT', po_number='ZK/PO/7790',
                                                     ship_date='2027-03-01', season='AW-27'), 'A: every field holds the NEW file\'s value'
    assert {k: (o['provenance'].get(k) or {}).get('cell') for k in ZEN_FIELDS} == ZEN_FIELDS, 'A: every value keeps its source cell'
    assert [(l['color'], [(s['size'], s['quantity']) for s in l['sizes']]) for l in o['lines']] == SECOND_LINES, \
        'A: all colour lines of the new file (3 here, 2 in the first file) are read'
    assert unit(o) == ('PACK', 3, 'sample'), f'§6: applying the template restores qty_unit, pieces_per_unit and quantity_basis: {unit(o)}'
    leaked = sorted({v for s in strings(d['orders'], skip=('warnings', 'notes')) for v in FIRST_ZEN_VALUES if v.upper() in s.upper()})
    assert not leaked, f'§1: a template carries layout only, never data; first-file values found: {leaked}'
    assert c.get('/api/templates').json()[0]['uses'] == 1, 'A: the template use is counted'

    p = preview(c, d)
    outlined = errors(p['plan'])
    assert set(outlined) <= {'orders.0.order_ref', 'orders.0.order_date'}, \
        f'A: nothing is outlined except values genuinely missing in the file (OC no, order date): {outlined}'
    p = preview(c, edit(c, p, order_ref='TEST/ACC/A1', order_date='2026-10-17'))
    assert not p['plan']['errors'], f'A: once the missing values are entered there are no questions: {p["plan"]["errors"]}'
    assert p['plan']['totals']['pieces'] == SECOND_PIECES, f'A: restored packs of 3 are applied to the pieces: {p["plan"]["totals"]}'


def test_A_saving_the_layout_again_creates_a_new_version(env):
    c, _ = env
    zen_template(c)
    d = upload(c, zen_kids_po(**SECOND_ZEN), 'zen-2.xlsx')
    r = map_layout(c, d, ZEN_DEFINITION, save_template=True, template_name='ZEN KIDS PO', qty_unit='PACK', pieces_per_unit=6, quantity_basis='sample')
    assert r.status_code == 200, r.text
    versions = sorted(((t.get('version'), t['active']) for t in c.get('/api/templates').json() if t['name'] == 'ZEN KIDS PO'), key=str)
    assert versions == [(1, False), (2, True)], f'§6: saving the same name again creates version 2, deactivates version 1 and keeps it: {versions}'
    o = upload(c, zen_kids_po(style='ZK-3003', po='ZK/PO/7801'), 'zen-3.xlsx')['orders']
    assert o and unit(o[0])[:2] == ('PACK', 6), f'§6: the next file is read with the active version (packs of 6): {o and unit(o[0])}'


# --- B: new buyer ----------------------------------------------------------------------------------------
BLUE_RENAMED = dict(buyer='BLUE BAY', style='BB-3300', description='GIRLS SUNDRESS', po='BB/PO/0042', ship=datetime(2027, 4, 2), season='HS-27',
                    lines=(('TEAL', '', (8, 12, 16, 20)), ('CORAL', '', (4, 6, 8, 10))), title='BLUE BAY LTD', ship_label='Order Date')
TABLE_FIELDS = ('size', 'quantity', 'color', 'qty_unit')
NOT_BLUE = FIRST_ZEN_VALUES +('ZEN KIDS', 'TESTBUYER', 'T1001', 'TEST BODYSUIT', 'TC100-1', 'TESTMERCH', 'WHITE AOP', 'PINK SOLID')
B_VARIANTS = {
    # labels moved and renamed: position-aware layout similarity is low
    'moved-and-renamed': (blue_bay_po, 9, {'buyer': 'BLUE BAY', 'style': 'BB-3300', 'po_number': 'BB/PO/0042', 'ship_date': '2027-04-02'}),
    # same look as ZEN KIDS, only the label of one anchor differs ("Order Date" where ZEN has "Ship Date")
    'one-anchor-renamed': (lambda: zen_kids_po(**BLUE_RENAMED), 11, {'buyer': 'BLUE BAY', 'style': 'BB-3300', 'po_number': 'BB/PO/0042'}),
}


@pytest.mark.parametrize('variant', list(B_VARIANTS))
def test_B_new_buyer_gets_no_template_and_no_data_from_another_buyer(env, variant):
    c, _ = env
    build, header_row, expected = B_VARIANTS[variant]
    upload(c, merch_bom(), 'earlier-bom.xlsx')   # an unrelated earlier draft
    zen_template(c)
    d = upload(c, build(), 'bluebay.xlsx')
    assert 'ZEN KIDS PO' not in d['format'] and c.get('/api/templates').json()[0]['uses'] == 0, \
        f'§6: ZEN KIDS template must not be applied to BLUE BAY ({variant}); format "{d["format"]}"'
    s = d.get('suggestion') or {}
    data = strings(d['orders'], skip=('warnings', 'notes')) + strings(s, skip=('explanation', 'evidence', 'reason', 'notes', 'template', 'templates'))
    leaked = sorted({v for x in data for v in NOT_BLUE if v.upper() in x.upper()})
    assert not leaked, f'B: no value from buyer ZEN KIDS or any earlier draft may appear in the new draft: {leaked}'

    sheet = next(x for x in d['sheets'] if x['name'] == s.get('sheet', d['sheets'][0]['name']))
    proposals = s.get('fields') or {}
    assert proposals, '§3: an unknown layout gets matcher proposals in draft.suggestion.fields'
    chosen = (s.get('definition') or {}).get('fields') or {}
    for field, p in proposals.items():
        if field in TABLE_FIELDS:   # columns are described by suggestion.table
            continue
        where = f'B: suggestion.fields.{field} = {p}'
        assert str(value_at(sheet, p.get('cell'))) == str(p.get('value')) and p.get('value') not in (None, ''), f'{where}: must point at a filled cell of THIS file holding the shown value'
        assert norm(value_at(sheet, p.get('label_cell'))) == norm(p.get('label')) != '', f'{where}: label_cell must hold the label shown'
        (lr, lc), (vr, vc) = rc(p['label_cell']), rc(p['cell'])
        assert (lr == vr and 1 <= vc - lc <= 3) or (lc == vc and 1 <= vr - lr <= 2), f'{where}: the value is right of (≤3) or below (≤2) its label (§3 rule 1)'
        assert p.get('evidence') and any(norm(p['label']) in norm(e) for e in p['evidence']), f'{where}: evidence must quote the label that justifies the match'
        assert p.get('source') in ('matcher', 'ai', 'both') and p.get('confidence') in ('high', 'medium', 'low'), f'{where}: source and confidence (§3)'
        if p['confidence'] == 'low':
            assert chosen.get(field) != p['cell'], f'{where}: low-confidence proposals are shown, never pre-selected (§3 rule 5)'
    found = {k: str((proposals.get(k) or {}).get('value')) for k in expected}
    assert found == expected, f'B: fields whose labels are buyer aliases are proposed from the new file: {found}'
    assert {k: chosen.get(k) for k in expected} == {k: proposals[k]['cell'] for k in expected}, 'B: exact-alias matches with valid values are pre-selected in suggestion.definition'
    assert ((s.get('definition') or {}).get('table') or {}).get('header_row') == header_row, f'B: the size table of the new file starts at row {header_row}'
    if variant == 'one-anchor-renamed':
        assert chosen.get('ship_date') != 'B8', 'B: the cell labelled "Order Date" must not be pre-selected as Ship date'


# --- C: missing unit -----------------------------------------------------------------------------------
C_FORMATS = {'xlsx': (lambda b: b, 'orders.xlsx'), 'csv': (as_csv, 'orders.csv'), 'json': (as_json_records, 'orders.json')}
C_PIECES = 500 + 520 + 480 + 510 + 490


@pytest.mark.parametrize('fmt', list(C_FORMATS))
def test_C_missing_unit_is_asked_and_blocks_saving_until_answered(env, fmt):
    c, oracle = env
    convert, name = C_FORMATS[fmt]
    d = upload(c, convert(flat_qty_orders()), name)
    assert len(d['orders']) == 1, f'C: the flat Style/Colour/Size/Qty file ({fmt}) is read as one order; format "{d["format"]}"'
    o = d['orders'][0]
    assert 'qty_unit' in o and o['qty_unit'] in ('', None), f'§5: the file says only "Qty", so qty_unit must exist and be empty, never assumed: {o.get("qty_unit")!r}'
    d = edit(c, d, buyer='FLAT BUYER', description='BASIC TEE', po_number='FB/PO/1', order_ref='TEST/ACC/C1',
             order_date='2026-10-17', ship_date='2026-12-15', quantity_basis='sample')
    p = preview(c, d); e = errors(p['plan'])
    assert 'orders.0.qty_unit' in e, f'§5: the planner raises the unit question on orders.0.qty_unit: {e}'
    assert 'piece' in ' '.join(e['orders.0.qty_unit']).lower() and 'pack' in ' '.join(e['orders.0.qty_unit']).lower(), \
        f'§5: the question asks pieces or packs: {e["orders.0.qty_unit"]}'
    assert accept(c, p).status_code == 409 and not oracle.rows, 'C: saving is blocked while the unit is unanswered'

    p = preview(c, edit(c, p, qty_unit='PACK', pieces_per_unit=None)); e = errors(p['plan'])
    assert 'orders.0.qty_unit' not in e and 'orders.0.pieces_per_unit' in e, f'§5: Packs needs pieces per pack, asked on orders.0.pieces_per_unit: {e}'
    assert 'pack' in ' '.join(e['orders.0.pieces_per_unit']).lower(), f'§5: "How many pieces are in one pack?": {e["orders.0.pieces_per_unit"]}'
    p = preview(c, edit(c, p, qty_unit='PACK', pieces_per_unit=1))
    assert 'orders.0.pieces_per_unit' in errors(p['plan']), '§5: a pack holds at least 2 pieces'
    p = preview(c, edit(c, p, qty_unit='PACK', pieces_per_unit=6))
    assert not p['plan']['errors'] and p['plan']['totals']['pieces'] == 6 * C_PIECES, f'§5: packs of 6 → pieces ×6: {p["plan"]["errors"]}'

    p = preview(c, edit(c, p, qty_unit='PCS', pieces_per_unit=6))
    assert p['plan']['totals']['pieces'] == C_PIECES or 'orders.0.pieces_per_unit' in errors(p['plan']), \
        '§5: PCS means pieces_per_unit = 1; a stale pack size is never multiplied in silently'
    p = preview(c, edit(c, p, qty_unit='PCS', pieces_per_unit=None))
    assert not p['plan']['errors'] and p['plan']['totals']['pieces'] == C_PIECES, f'§5: choosing Pieces answers the question: {p["plan"]["errors"]}'
    r = accept(c, p)
    assert r.status_code == 200, f'C: once answered, the order saves: {r.text[:300]}'
    assert sum(x['values']['RQTY'] for x in r.json()['rows'] if x['table'] == 'ORDSIZE') == C_PIECES


@pytest.mark.parametrize('header', ['Pcs', 'Pieces'])
def test_C_unit_is_taken_from_the_file_when_the_header_says_pieces(env, header):
    c, _ = env
    o = upload(c, flat_qty_orders(header), 'orders.xlsx')['orders']
    assert o and unit(o[0])[:2] == ('PCS', 1), f'§5: a quantity header "{header}" is evidence of pieces: {o and unit(o[0])}'
    assert 'D1' in json.dumps(o[0]['provenance'].get('qty_unit')), f'§5: provenance records the evidence cell D1: {o[0]["provenance"].get("qty_unit")}'


# --- D: wrong mapping ----------------------------------------------------------------------------------
D_FIELDS = {'buyer': 'B1', 'style': 'B2', 'po_number': 'B3'}
D_TABLE = {'type': 'flat', 'header_row': 5, 'columns': {'color': 1, 'size': 2, 'quantity': 3}}
D_WRONG = {'fields': dict(D_FIELDS, ship_date='H3'), 'table': {'type': 'flat', 'header_row': 5, 'columns': {'color': 1, 'size': 2, 'quantity': 4}}}


def matcher_check():
    try:
        from backend import matcher
    except ImportError as exc:
        pytest.fail(f'§4: backend/matcher.py with check(sheet, definition, fields) -> (ok_definition, rejected) is required ({exc})')
    return matcher.check


def effective_fields(c):
    r = c.get('/api/fields')
    assert r.status_code == 200, f'§2: GET /api/fields lists the effective fields: {r.status_code}'
    return r.json()


def test_D_wrong_person_mapping_is_refused_with_409_naming_each_field(env):
    c, _ = env
    d = upload(c, wrong_mapping_po(), 'acme.xlsx')
    r = map_layout(c, d, D_WRONG, save_template=True, template_name='ACME PO')
    assert r.status_code == 409, f'§4: /map answers 409 for Ship date → "NEXT" and Quantity → the Remarks text column: {r.status_code} {r.text[:300]}'
    text = json.dumps(r.json()).lower()
    assert 'ship' in text and 'next' in text, f'§4: the 409 names Ship date and the offending value "NEXT": {r.json()}'
    assert 'quantity' in text or 'qty' in text, f'§4: the 409 also names Quantity: {r.json()}'
    after = c.get(f'/api/drafts/{d["id"]}').json()
    assert after['version'] == d['version'] and after['orders'] == d['orders'], 'D: a refused mapping changes nothing in the draft'
    assert c.get('/api/templates').json() == [], 'D: a refused mapping saves no template'
    r = map_layout(c, d, {'fields': dict(D_FIELDS, ship_date='H1'), 'table': D_TABLE})
    assert r.status_code == 200, f'D: the corrected mapping is accepted: {r.text[:300]}'
    o = r.json()['orders'][0]
    assert (o['buyer'], o['style'], o['po_number'], o['ship_date']) == ('ACME KIDS', 'AC-77', 'AC/PO/31', '2027-02-01')
    assert sorted(s['quantity'] for l in o['lines'] for s in l['sizes']) == [110, 120, 140]


def test_D_validator_rejects_wrong_ai_fields_and_keeps_the_rest(env):
    c, _ = env
    d = upload(c, wrong_mapping_po(), 'acme.xlsx')
    proposal = {'fields': dict(D_FIELDS, season='H2', ship_date='H3'), 'table': deepcopy(D_WRONG['table'])}
    ok, rejected = matcher_check()(d['sheets'][0], deepcopy(proposal), effective_fields(c))
    assert ok['fields'] == dict(D_FIELDS, season='H2'), f'§4: valid fields are kept, ship_date → H3 dropped: {ok["fields"]}'
    cols = (ok.get('table') or {}).get('columns') or {}
    assert cols.get('quantity') != 4 and {k: cols.get(k) for k in ('color', 'size')} == {'color': 1, 'size': 2}, \
        f'§4: the text column is not kept as Quantity; the valid size/colour columns are: {ok.get("table")}'
    by = {x.get('field'): x for x in rejected}
    assert set(by) == {'ship_date', 'quantity'}, f'§4: exactly the two wrong fields are rejected: {rejected}'
    assert (by['ship_date'].get('cell'), by['ship_date'].get('value')) == ('H3', 'NEXT') and \
        'next' in by['ship_date']['reason'].lower() and 'date' in by['ship_date']['reason'].lower(), \
        f'§3/§4: rejection names cell, value and reason (H3 holds "NEXT", which is not a date): {by["ship_date"]}'
    assert re.search(r'number|numeric', by['quantity'].get('reason', ''), re.I), f'§4: the quantity rejection says the column is not numbers: {by["quantity"]}'


RULES = [  # (rule, header fields to add, table change, field that must be rejected)
    ('empty cell', {'ship_date': 'C1'}, None, 'ship_date'),
    ('cell is itself a label', {'season': 'G2'}, None, 'season'),
    ('date field without a date', {'ship_date': 'H3'}, None, 'ship_date'),
    ('buyer that is a date', {'buyer': 'H1'}, None, 'buyer'),
    ('buyer that is a number', {'buyer': 'C6'}, None, 'buyer'),
    ('same cell for two fields', {'description': 'B2'}, None, ('style', 'description')),
    ('text quantity column', {}, {'quantity': 4}, 'quantity'),
    ('negative quantity', {}, 'negative', 'quantity'),
    ('column outside the sheet', {}, {'color': 40}, 'color'),
    ('size header without sizes', {}, {'type': 'matrix', 'header_row': 5, 'color_col': 1, 'first_size_col': 2, 'last_size_col': 4}, None),
]


@pytest.mark.parametrize('rule,fields,table,field', RULES, ids=[r[0] for r in RULES])
def test_D_validator_rule(env, rule, fields, table, field):
    c, _ = env
    sheet = deepcopy(upload(c, wrong_mapping_po(), 'acme.xlsx')['sheets'][0])
    t = deepcopy(D_TABLE)
    if table == 'negative':
        next(x for x in sheet['cells'] if x['cell'] == 'C7')['value'] = -140
    elif isinstance(table, dict) and 'type' in table:
        t = table
    elif table:
        t['columns'].update(table)
    definition = {'fields': dict(D_FIELDS, **fields), 'table': t}
    ok, rejected = matcher_check()(sheet, deepcopy(definition), effective_fields(c))
    names = {x.get('field') for x in rejected}
    assert rejected and all(str(x.get('reason') or '').strip() for x in rejected), f'§4 {rule}: rejected with a reason: {rejected}'
    if isinstance(field, tuple):
        assert names & set(field) and list((ok.get('fields') or {}).values()).count('B2') == 1, f'§4 {rule}: one cell, one field: {ok}, {rejected}'
    elif field is None:
        assert ((ok.get('table') or {}).get('type'), (ok.get('table') or {}).get('header_row')) != ('matrix', 5), f'§4 {rule}: the matrix is rejected: {ok}'
    else:
        assert field in names, f'§4 {rule}: {field} must be rejected: {rejected}'
        kept = (ok.get('fields') or {}).get(field) if field in definition['fields'] else ((ok.get('table') or {}).get('columns') or {}).get(field)
        bad = definition['fields'].get(field) or t['columns'].get(field)
        assert kept != bad, f'§4 {rule}: the rejected {field} is not kept in ok_definition'
    if not isinstance(field, tuple):
        assert (ok.get('fields') or {}).get('style') == 'B2', f'§4 {rule}: valid fields are kept: {ok}'


def test_D_ai_proposal_is_cross_checked_and_wrong_fields_rejected(env, monkeypatch):
    c, _ = env
    d = upload(c, wrong_mapping_po(), 'acme.xlsx')
    monkeypatch.setattr(ai, 'available', lambda: True)
    monkeypatch.setattr(ai, 'status', lambda: dict(available=True, models=['test'], model='test'))
    monkeypatch.setattr(ai, '_chat', lambda *a, **k: json.dumps(dict(D_WRONG, explanation='Mapped from the header block.')))
    r = c.post(f'/api/drafts/{d["id"]}/suggest', json={'sheet': 'PO'})
    assert r.status_code == 200, r.text
    s = r.json(); chosen = s['definition']['fields']
    assert chosen.get('ship_date') != 'H3' and ((s['definition'].get('table') or {}).get('columns') or {}).get('quantity') != 4, \
        f'§1/§4: an AI proposal is validated before it is offered; Ship date → "NEXT" and Quantity → Remarks must not survive: {s["definition"]}'
    assert {k: chosen.get(k) for k in D_FIELDS} == D_FIELDS, f'§4: the valid part of the AI proposal is kept: {chosen}'
    rejected = {(x.get('field'), x.get('source')) for x in s.get('rejected') or []}
    assert {('ship_date', 'ai'), ('quantity', 'ai')} <= rejected, f'§3: suggestion.rejected lists the AI fields with source "ai": {s.get("rejected")}'


# --- E: approved order ---------------------------------------------------------------------------------
def master_labels(receipt):
    masters = {m['table']: m for m in registry.effective()['masters'].values()}
    return {(r['table'], r['pk']): r['values'][masters[r['table']]['label']]
            for r in receipt['rows'] + receipt['reused'] if r['table'] in masters}


def check_saved_rows(receipt, ref, po=SECOND_ZEN['po']):
    """ORDMAS / ORDSTYLE / ORDSIZE counts, links and totals agree with each other and with the file."""
    rows = {}
    for r in receipt['rows']:
        rows.setdefault(r['table'], []).append(r)
    labels = master_labels(receipt)
    [head] = rows['ORDMAS']; lines = rows['ORDSTYLE']; sizes = rows['ORDSIZE']
    h = head['values']
    assert (h['DOCID'], h['BONO'], str(h['ODATE'])[:10], h['OQTY']) == (ref, po, '2026-10-17', SECOND_PIECES), f'E: order header {h}'
    assert labels[('BUYMAS', h['BUYER'])] == 'ZEN KIDS' and labels[('STYLENUM', h['STYLENO'])] == SECOND_ZEN['style']
    assert len(lines) == 3 and len(sizes) == 12, f'E: one ORDSTYLE per colour line (3) and one ORDSIZE per size cell (12): {len(lines)}, {len(sizes)}'
    want = {colour: {size: q * 3 for size, q in qty} for colour, qty in SECOND_LINES}
    got = {}
    for line in lines:
        v = line['values']
        assert v['ORDMASID'] == head['pk'] and str(v['SHIPDT'])[:10] == '2027-03-01', f'E: line links to its order and keeps the ship date: {v}'
        mine = [s['values'] for s in sizes if s['values']['SUB3_SUBNO'] == v['SUBNO']]
        assert all(s['ORDMASID'] == head['pk'] for s in mine) and sum(s['RQTY'] for s in mine) == v['ORDQTY'], f'E: line {v["SUBNO"]} total = its size rows'
        got[labels[('COLORMAS', v['COMBO'])]] = {labels[('SIZE_MAS', s['GSIZE'])]: s['RQTY'] for s in mine}
    assert got == want, f'E: every size quantity of the file is saved as pieces (packs × 3): {got}'
    assert sum(l['values']['ORDQTY'] for l in lines) == h['OQTY'] == receipt['totals']['pieces'] == SECOND_PIECES
    return head


def test_E_approved_order_is_one_transaction_and_every_row_reads_back_equal(env, monkeypatch):
    c, oracle = env
    calls = []; commit = oracle_store.commit
    monkeypatch.setattr(oracle_store, 'commit', lambda *a, **k: calls.append(a[0]['id']) or commit(*a, **k))
    p, receipt = approved_zen_order(c, 'TEST/ACC/E1')
    assert calls == [p['id']], f'E: one approval, one transaction: {calls}'
    for r in receipt['rows']:
        assert oracle.rows[r['table']][r['pk']] == r['values'], f'E: {r["table"]} {r["pk"]} reads back exactly as inserted'
    head = check_saved_rows(receipt, 'TEST/ACC/E1')
    g = c.get(f'/api/history/{p["id"]}/graph').json()
    assert g['verified'] and all(x['found'] and x['matches'] for n in g['nodes'] for x in n['records']), 'E: the read-back graph verifies every row'
    again = accept(c, p).json()
    assert again.get('already_committed') and calls == [p['id']] and list(oracle.rows['ORDMAS']) == [head['pk']], 'E: a retried approval never inserts twice'


@REAL
def test_E_F_real_oracle_order_is_saved_read_back_and_opens_in_database(real):
    c = real; tok = secrets.token_hex(4).upper(); ref = f'TEST-ACC-{tok}'
    p, receipt = approved_zen_order(c, ref, zen_kids_po(**dict(SECOND_ZEN, po=f'TEST-PO-{tok}')))
    assert not receipt['already_committed']
    head = check_saved_rows(receipt, ref, po=f'TEST-PO-{tok}')
    g = c.get(f'/api/history/{p["id"]}/graph').json()
    assert g['verified'], f'E: every row read back from Oracle equals what was written: {[n["table"] for n in g["nodes"] if not n["verified"]]}'
    with oracle_store.connect() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*), SUM(OQTY) FROM {OWNER}.ORDMAS WHERE DOCID = :r', r=ref)
        assert cur.fetchone() == (1, SECOND_PIECES)
        cur.execute(f'''SELECT COUNT(DISTINCT l.ORDSTYLEID), COUNT(s.ORDSIZEID), SUM(s.RQTY) FROM {OWNER}.ORDSTYLE l
                        JOIN {OWNER}.ORDSIZE s ON s.ORDMASID = l.ORDMASID AND s.SUB3_SUBNO = l.SUBNO WHERE l.ORDMASID = :id''', id=head['pk'])
        assert cur.fetchone() == (3, 12, SECOND_PIECES), 'E: Oracle holds 3 lines and 12 size rows totalling the order quantity'
        cur.execute('SELECT COUNT(*) FROM PP_IMPORT_LOG WHERE JOBID = :id', id=p['id'])
        assert cur.fetchone()[0] == 1
    rel = opened_order(c, ref)
    check_order_view(rel, 'ZEN KIDS', lines=3, pieces=SECOND_PIECES, colours={'WHITE', 'PINK', 'LEMON'}, sizes={'S', 'M', 'L', 'XL'})


# --- F: visual verification (Database screen) -----------------------------------------------------------
def opened_order(c, ref):
    """Database screen: search Order (ORDMAS) by order reference, then open the record with its links."""
    r = c.get('/api/explorer/tables/ORDMAS', params={'column': 'DOCID', 'value': ref})
    assert r.status_code == 200, f'F: order search: {r.status_code} {r.text[:300]}'
    found = [x for x in r.json()['rows'] if x['DOCID'] == ref]
    assert len(found) == 1, f'F: searching {ref} finds exactly that order: {r.json()["rows"]}'
    r = c.get(f'/api/explorer/related/ORDMAS/{found[0]["ORDMASID"]}')
    assert r.status_code == 200, f'F: opening the order record: {r.status_code} {r.text[:300]}'
    return r.json()


def check_order_view(rel, buyer, lines, pieces, colours=None, sizes=None):
    rec = rel['record']; by = {g['table']: g for g in rel['related']}
    assert {'ORDSTYLE', 'ORDSIZE', 'BUYMAS'} <= set(by), f'F: the order opens with its lines, size rows and buyer: {sorted(by)}'
    assert by['ORDSTYLE']['direction'] == by['ORDSIZE']['direction'] == 'down' and by['BUYMAS']['direction'] == 'up'
    assert rel['decoded'].get('BUYER', {}).get(str(rec['BUYER'])) == buyer == by['BUYMAS']['rows'][0]['BUYERNAME'], \
        f'F: the buyer ID is decoded to the buyer name: {rel["decoded"].get("BUYER")}'
    st, sz = by['ORDSTYLE'], by['ORDSIZE']
    assert len(st['rows']) == lines and sum(r['ORDQTY'] for r in st['rows']) == rec['OQTY'] == pieces, \
        f'F: {lines} ORDSTYLE lines whose quantities add up to the order quantity {pieces}'
    combo = {st['decoded'].get('COMBO', {}).get(str(r['COMBO'])) for r in st['rows']}
    gsize = {sz['decoded'].get('GSIZE', {}).get(str(r['GSIZE'])) for r in sz['rows']}
    assert None not in combo and None not in gsize, f'F: colour and size IDs are decoded to master names: {combo}, {gsize}'
    assert colours is None or combo == colours, f'F: decoded colours {combo}'
    assert sizes is None or gsize == sizes, f'F: decoded sizes {gsize}'
    assert all(r['ORDMASID'] == rec['ORDMASID'] for r in st['rows'] + sz['rows'])
    if not sz['more']:
        assert sum(r['RQTY'] for r in sz['rows']) == pieces, 'F: size rows add up to the order quantity'


def test_F_saved_order_opens_in_database_with_lines_sizes_and_decoded_masters(world):
    c = world
    _, receipt = approved_zen_order(c, 'TEST/ACC/F1')
    rel = opened_order(c, 'TEST/ACC/F1')
    assert rel['record']['ORDMASID'] == receipt['orders'][0]['ordmasid']
    check_order_view(rel, 'ZEN KIDS', lines=3, pieces=SECOND_PIECES, colours={'WHITE', 'PINK', 'LEMON'}, sizes={'S', 'M', 'L', 'XL'})


@REAL
def test_F_real_oracle_existing_order_opens_with_lines_sizes_and_decoded_masters(real):
    c = real; ref = 'PK/26-27/S/9101'
    with oracle_store.connect() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT ORDMASID, OQTY FROM {OWNER}.ORDMAS WHERE DOCID = :r', r=ref)
        row = cur.fetchone()
        if not row:
            pytest.skip(f'{ref} is not in this replica')
        cur.execute(f'SELECT COUNT(*) FROM {OWNER}.ORDSTYLE WHERE ORDMASID = :id', id=row[0]); lines = cur.fetchone()[0]
        cur.execute(f'SELECT b.BUYERNAME FROM {OWNER}.ORDMAS o JOIN {OWNER}.BUYMAS b ON b.BUYMASID = o.BUYER WHERE o.ORDMASID = :id', id=row[0])
        buyer = cur.fetchone()[0]
    check_order_view(opened_order(c, ref), buyer, lines=lines, pieces=row[1])
