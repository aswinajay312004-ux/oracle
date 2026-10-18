"""Master matching (backend/erp/masters.py): a value from a file reuses the existing master record when only case,
spacing or punctuation differ; a really new value is created, with similar existing names shown before approval."""
from copy import deepcopy
import pytest
from backend import workbooks
from backend.erp import catalog, mapping, masters, planner


def order(**changes):
    o = workbooks.base_order()
    o.update(buyer='NEXT LTD', style='ZK-2002', description='BABY SLEEPSUIT', po_number='PO-1', order_ref='TEST/M1',
             order_date='2026-10-17', ship_date='2026-11-20', qty_unit='PCS', pieces_per_unit=1, quantity_basis='sample',
             sample_type='DEVELOPMENT SAMPLE',
             lines=[dict(label='1', color='NAVY BLUE', declared_total=10, sizes=[dict(size='M', quantity=4), dict(size='XL', quantity=6)])])
    o.update(changes)
    return o


MASTERS = dict(BUYMAS=[dict(id=1, label='NEXT LTD', cancelled=False), dict(id=2, label='ZARA', cancelled=False)],
               STYLENUM=[dict(id=10, label='ZK-2002', cancelled=False)], STYLEMAS=[dict(id=20, label='BABY SLEEPSUIT', cancelled=False)],
               COLORMAS=[dict(id=30, label='NAVY BLUE', cancelled=False), dict(id=31, label='LEMON', cancelled=False)],
               SIZE_MAS=[dict(id=40, label='M', cancelled=False), dict(id=41, label='XL', cancelled=False)],
               UNITMAS=[dict(id=50, label='PCS', cancelled=False)])


def plan(o, masters_=MASTERS):
    return planner.plan(catalog.SnapshotCatalog(masters=deepcopy(masters_)), [o], 'h' * 64, mapping.effective())


def ops(p, table):
    return [op for op in p['operations'] if op['table'] == table]


@pytest.mark.parametrize('a,b', [('Next Ltd.', 'NEXT LTD'), ('ZK 02002', 'ZK-2002'), ('M & S', 'M and S'), ('navy-blue', 'NAVY BLUE')])
def test_fingerprint_ignores_case_spacing_punctuation_and_leading_zeros(a, b):
    assert masters.fingerprint(a) == masters.fingerprint(b)


def test_similarity_reads_names_as_sets_of_words():
    assert masters.similarity('NAVY BLUES', 'NAVY BLUE') >= 0.9 and masters.similarity('LEMMON', 'LEMON') >= 0.9
    assert masters.similarity('MARKS AND SPENSER', 'Marks & Spencer') >= 0.9
    assert masters.similarity('PINK', 'LEMON') < 0.5 and masters.similarity('BABY SLEEPSUIT', 'BABY ROMPER') < 0.8


def test_a_value_written_differently_reuses_the_existing_master_and_says_so():
    p = plan(order(buyer='Next Ltd.', style='zk 2002', lines=[dict(label='1', color='navy-blue', declared_total=10,
                                                                   sizes=[dict(size='M', quantity=10)])]))
    assert not p['errors'], p['errors']
    assert [(op['action'], op['values']['BUYMASID']) for op in ops(p, 'BUYMAS')] == [('reuse', 1)]
    assert [(op['action'], op['values']['STYLENUMID']) for op in ops(p, 'STYLENUM')] == [('reuse', 10)]
    assert [(op['action'], op['values']['COLORMASID']) for op in ops(p, 'COLORMAS')] == [('reuse', 30)]
    notes = {i['path']: i['message'] for i in p['issues'] if i['severity'] == 'warning'}
    assert 'existing buyer “NEXT LTD” (BUYMAS record 1)' in notes['orders.0.buyer']
    assert 'existing colour / combo “NAVY BLUE”' in notes['orders.0.lines.0.color']


def test_a_new_name_close_to_an_existing_one_is_created_with_a_note_not_blocked():
    p = plan(order(lines=[dict(label='1', color='NAVY BLUES', declared_total=10, sizes=[dict(size='M', quantity=10)])]))
    assert not p['errors'], p['errors']
    new = [op for op in ops(p, 'COLORMAS') if op['action'] == 'insert']
    assert [op['values']['COLOR'] for op in new] == ['NAVY BLUES']
    note = next(i for i in p['issues'] if i['path'] == 'orders.0.lines.0.color')
    assert note['severity'] == 'warning' and 'Similar existing: “NAVY BLUE”' in note['message']


def test_short_codes_and_style_numbers_are_never_guessed():
    p = plan(order(style='ZK-2003', lines=[dict(label='1', color='LEMON', declared_total=10, sizes=[dict(size='X-L', quantity=10)])]))
    assert not p['errors'], p['errors']
    assert [op['values']['SIZES'] for op in ops(p, 'SIZE_MAS') if op['action'] == 'insert'] == ['X-L']     # ≤3 characters: exact only
    assert [op['values']['STYLENUMBER'] for op in ops(p, 'STYLENUM') if op['action'] == 'insert'] == ['ZK-2003']
    assert not [i for i in p['issues'] if i['severity'] == 'warning' and i['path'] in ('orders.0.style', 'orders.0.lines.0.sizes.0.size')]


def test_a_value_matching_two_existing_records_must_be_typed_exactly():
    two = dict(MASTERS, BUYMAS=MASTERS['BUYMAS'] + [dict(id=3, label='NEXT, LTD', cancelled=False)])
    p = plan(order(buyer='Next Ltd.'), two)
    err = next(i for i in p['issues'] if i['path'] == 'orders.0.buyer')
    assert err['severity'] == 'error' and '“NEXT LTD”' in err['message'] and '“NEXT, LTD”' in err['message']
    assert not plan(order(buyer='NEXT LTD'), two)['errors']                                              # the exact name is fine
