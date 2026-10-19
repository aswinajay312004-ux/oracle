"""Database screen, Orders: orders in Oracle by business name, and a saved order compared value by value with the Excel
file it came from (backend/erp/order_view.py)."""
import secrets
from test_acceptance import world, real, approved_zen_order, zen_kids_po, SECOND_ZEN, SECOND_PIECES, REAL  # noqa: F401  (fixtures)


def test_order_list_is_newest_first_by_business_name_and_marks_poppys_orders(world):  # noqa: F811
    c = world
    _, receipt = approved_zen_order(c, 'TEST/OV/1')
    ordmasid = receipt['orders'][0]['ordmasid']
    page = c.get('/api/orders').json()
    first = page['orders'][0]
    assert first['id'] == ordmasid and first['order_ref'] == 'TEST/OV/1'
    assert first['buyer'] == 'ZEN KIDS' and first['pieces'] == SECOND_PIECES        # names, not record IDs
    assert first['poppys']['filename'] == 'zen-po.xlsx'
    assert all(o['poppys'] is None for o in page['orders'][1:])                    # the demo's own orders
    for q in ('ov/1', 'zen kids'):
        assert [o['id'] for o in c.get('/api/orders', params={'q': q}).json()['orders']] == [ordmasid], q
    assert c.get('/api/orders', params={'q': 'NO SUCH ORDER 100%'}).json()['orders'] == []
    older = c.get('/api/orders', params={'after': ordmasid}).json()['orders']
    assert older and all(o['id'] < ordmasid for o in older)


def test_saved_order_is_compared_with_the_excel_value_by_value(world):  # noqa: F811
    c = world
    _, receipt = approved_zen_order(c, 'TEST/OV/2')
    ordmasid = receipt['orders'][0]['ordmasid']
    d = c.get(f'/api/orders/{ordmasid}').json()
    sheet, check = d['sheet'], d['saved']['check']
    assert sheet['order_ref'] == 'TEST/OV/2' and sheet['total'] == SECOND_PIECES and sheet['verified'] is True
    assert {l['colour'] for l in sheet['lines']} == {'WHITE', 'PINK', 'LEMON'}
    assert check['same'] and check['pieces'] == {'excel': SECOND_PIECES, 'oracle': SECOND_PIECES}
    assert check['cells']['count'] == 12 and not check['cells']['different'] and check['details']['count'] > 5
    assert d['saved']['filename'] == 'zen-po.xlsx'
    excel = d['saved']['excel']                                    # each Excel value with its cell, for ticking off
    rows = {r['label']: r for r in excel['from_file']}
    assert rows['Buyer']['excel'] == rows['Buyer']['oracle'] == 'ZEN KIDS' and rows['Buyer']['cell']
    assert all(r['state'] == 'same' for r in excel['from_file']), excel['from_file']
    assert {r['label']: r['why'] for r in excel['filled']}['Order reference (OC number)'] == 'Typed in POPPYS'
    # someone changes one size quantity in Oracle afterwards: the screen shows the cell and what the Excel said
    conn = demo_db_connection(c)
    size = conn.execute('SELECT ORDSIZEID, RQTY FROM ORDSIZE WHERE ORDMASID = ? ORDER BY ORDSIZEID LIMIT 1', (ordmasid,)).fetchone()
    conn.execute('UPDATE ORDSIZE SET RQTY = ? WHERE ORDSIZEID = ?', (size[1] + 7, size[0])); conn.commit()
    d = c.get(f'/api/orders/{ordmasid}').json()
    assert d['sheet']['verified'] is False and not d['saved']['check']['same']
    assert {r['label']: r['state'] for r in d['saved']['excel']['from_file']}['Size quantities'] == 'different'
    [diff] = d['saved']['check']['cells']['different']
    assert diff['excel'] == size[1] and diff['oracle'] == size[1] + 7
    assert any(l.get('excel') for l in d['sheet']['lines'])


def test_an_order_entered_outside_poppys_opens_without_a_comparison(world):  # noqa: F811
    c = world
    other = next(o for o in c.get('/api/orders').json()['orders'] if not o['poppys'])
    d = c.get(f'/api/orders/{other["id"]}').json()
    assert d['saved'] is None and d['sheet']['verified'] is None and d['sheet']['lines']
    assert all(not str(f['value']).isdigit() or f['label'] not in ('Buyer', 'Colour / combo') for f in d['sheet']['header'])
    assert c.get('/api/orders/999999999').status_code == 404


def demo_db_connection(c):
    """The SQLite GARMENT_DEMO copy behind the world fixture (main.query_sources yields its executor)."""
    from backend import main
    with main.query_sources() as s:
        return s['db'][0].conn


@REAL
def test_real_oracle_saved_order_is_found_by_name_and_matches_the_excel(real):  # noqa: F811
    c = real; tok = secrets.token_hex(4).upper(); ref = f'TEST-OV-{tok}'
    _, receipt = approved_zen_order(c, ref, zen_kids_po(**dict(SECOND_ZEN, po=f'TEST-PO-{tok}')))
    ordmasid = receipt['orders'][0]['ordmasid']
    for q in (ref.lower(), f'po-{tok}'.lower()):
        found = c.get('/api/orders', params={'q': q}).json()['orders']
        assert [o['id'] for o in found] == [ordmasid], q
    assert found[0]['buyer'] == 'ZEN KIDS' and found[0]['pieces'] == SECOND_PIECES and found[0]['poppys']
    assert any(o['id'] == ordmasid for o in c.get('/api/orders', params={'q': 'zen kids'}).json()['orders'])
    d = c.get(f'/api/orders/{ordmasid}').json()
    assert d['sheet']['verified'] is True and d['saved']['check']['same'], d['saved']['check']
    assert d['saved']['check']['cells']['count'] == 12 and d['sheet']['total'] == SECOND_PIECES
    labels = {f['label']: f['value'] for f in d['sheet']['header']}
    assert labels['Buyer'] == 'ZEN KIDS' and labels['Order confirmation date'] == '2026-10-17'


def test_a_value_changed_before_saving_is_not_shown_as_the_excel_value():
    from backend.erp import mapping, order_view
    order = dict(provenance={'buyer': dict(cell='C2', value='Carters'), 'style': dict(cell='C4', value='ct27085 ')}, lines=[])
    sheet = dict(header=[dict(label='Buyer', value="CARTER'S"), dict(label='Style number', value='CT27085')], lines=[])
    check = dict(details=dict(different=[]), cells=dict(count=0, different=[]), pieces=dict(excel=0, oracle=0), missing_lines=[], extra_lines=[])
    rows = {r['label']: r['state'] for r in order_view.excel_map(mapping.effective(), order, sheet, {}, check)['from_file']}
    assert rows == {'Buyer': 'changed', 'Style number': 'same'}      # case and spaces are not a change
