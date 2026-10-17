"""Reading findings (a second date, a total row that disagrees, rows below the table, hidden rows) are not
left in a collapsed notes list: they are review items the planner raises as warnings on the field they
concern, so the approver sees them in "Please read before approving"."""
from datetime import datetime
from openpyxl import Workbook
from backend import workbooks
from backend.erp import planner, mapping, catalog
from fixtures import save


def book(rows):
    wb = Workbook(); ws = wb.active; ws.title = 'Orders'
    for r in rows:
        ws.append(list(r))
    return wb, ws


def ready(o):
    return dict(o, buyer='B', description='D', po_number='P', order_ref='R/77', order_date='2026-10-01', ship_date='2026-11-01',
                quantity_basis='sample', qty_unit='PCS', pieces_per_unit=1)


def plan(o):
    return planner.plan(catalog.SnapshotCatalog(), [o], 'f' * 64, mapping.effective())


def test_total_mismatch_is_a_warning_on_the_size_table_before_approval():
    wb, _ = book([['Style No', 'T-9'], [], ['Colour', 'Size', 'Qty'], ['RED', 'S', 100], ['RED', 'M', 200], ['Total', '', 310]])
    o = workbooks.parse(save(wb))['orders'][0]
    assert [r['field'] for r in o['review']] == ['lines']
    assert 'says the total is 310, but the rows read add up to 300' in o['review'][0]['message']
    p = plan(ready(o))
    found = [i for i in p['issues'] if i['path'] == 'orders.0.lines']
    assert found and found[0]['severity'] == 'warning' and 'says the total is 310' in found[0]['message']
    assert any('says the total is 310' in w for w in p['warnings'])          # listed under "Please read before approving"
    assert not p['errors']                                                    # a warning: the person decides


def test_second_date_is_a_warning_on_that_date_field():
    wb, _ = book([['Style', 'TD-1', None, 'Revised Ship Date', datetime(2027, 2, 1)], ['Order Date', datetime(2026, 10, 2)],
                  ['Ship Date', datetime(2027, 1, 15)], [], ['Colour', 'Size', 'Qty'], ['RED', 'S', 5]])
    o = workbooks.parse(save(wb))['orders'][0]
    assert [(r['field'], 'Revised Ship Date' in r['message']) for r in o['review']] == [('ship_date', True)]
    p = plan(dict(ready(o), ship_date=o['ship_date']))
    assert any(i['path'] == 'orders.0.ship_date' and i['severity'] == 'warning' for i in p['issues'])


def test_hidden_rows_with_data_are_a_review_item():
    wb, ws = book([['Style No', 'H-1'], [], ['Colour', 'Size', 'Qty'], ['RED', 'S', 10], ['RED', 'M', 99], ['RED', 'L', 20]])
    ws.row_dimensions[5].hidden = True
    o = workbooks.parse(save(wb))['orders'][0]
    assert [(s['size'], s['quantity']) for s in o['lines'][0]['sizes']] == [('S', 10), ('L', 20)]   # hidden row not read
    assert any(r['field'] == 'lines' and 'hidden' in r['message'].lower() for r in o['review'])


def test_clean_files_have_no_review_items():
    wb, _ = book([['Style No', 'C-1'], [], ['Colour', 'Size', 'Qty'], ['RED', 'S', 10], ['RED', 'M', 20], ['Total', '', 30]])
    o = workbooks.parse(save(wb))['orders'][0]
    assert not o.get('review')
    assert not [i for i in plan(ready(o))['issues'] if i['path'] == 'orders.0.lines']


def test_ask_on_review_keeps_what_the_file_said_about_the_unit():
    from types import SimpleNamespace
    from backend import main
    o = {'qty_unit': 'PCS', 'pieces_per_unit': 1, 'warnings': [],
         'provenance': {'qty_unit': {'sheet': 'PO', 'cell': 'C3', 'evidence': 'the quantity heading C3 says “Qty (pcs)”', 'reason': 'file'}}}
    main._confirm_unit(o, SimpleNamespace(qty_unit='', pieces_per_unit=None, quantity_basis=None))
    p = o['provenance']['qty_unit']
    assert o['qty_unit'] == '' and p['reason'] == 'review' and p['cell'] == 'C3'
    assert p['question'] == 'The quantity heading C3 says “Qty (pcs)” — are these quantities pieces or packs?'
    assert 'does not say' not in ' '.join(o['warnings'])


def test_big_flat_files_parse_in_linear_time():
    """Header lookups once re-normalised every cell per field and per order: 2,000 lines took minutes in the
    server process. With the per-sheet label index a 4,000-line buyer file parses in about a second."""
    import time
    rows = ['Style,Colour,Size,Qty'] + [f'ST-{i // 6},C{i % 3},{"SMLX"[i % 4]}{i % 7},{10 + i % 9}' for i in range(4000)]
    started = time.time()
    d = workbooks.parse('\n'.join(rows).encode(), filename='big.csv')
    assert len(d['orders']) == 667 and time.time() - started < 8


def test_european_thousands_are_never_guessed():
    """In a semicolon CSV that writes decimals with a comma, “2.000” is two thousand: it is never read as 2 (or
    silently as 2000). The cell stays as the file wrote it, is refused until the person types the number, and
    the size table says why."""
    d = workbooks.parse(b'sep=;\nStyle;Colour;Size;Qty\nA1;RED;S;2.000\nA1;RED;M;1.500\nA1;RED;L;12\n', filename='eu.csv')
    o = d['orders'][0]
    assert [(s['size'], s['quantity']) for s in o['lines'][0]['sizes']] == [('S', '2.000'), ('M', '1.500'), ('L', 12)]
    assert any(r['field'] == 'lines' and 'dot between thousands' in r['message'] for r in o['review'])
    p = plan(ready(o))
    bad = {i['path']: i['message'] for i in p['issues'] if i['severity'] == 'error'}
    assert '“2.000” could mean 2000' in bad['orders.0.lines.0.sizes.0.quantity']
    assert '“1.500” could mean 1500' in bad['orders.0.lines.0.sizes.1.quantity']
    fixed = ready(o); fixed['lines'][0]['sizes'][0]['quantity'] = 2000; fixed['lines'][0]['sizes'][1]['quantity'] = 1500
    assert not [i for i in plan(fixed)['issues'] if i['severity'] == 'error']
    assert plan(fixed)['totals']['pieces'] == 3512
