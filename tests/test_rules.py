"""Optional-field, mapping and schema-design rules. Pure tests against the live column
metadata snapshot in docs/ (no database needed)."""
from collections import defaultdict
import json
from pathlib import Path
import pytest
from backend import database, mapping, oracle_store as db, workbooks
from tests import factory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def registry():
    out = defaultdict(dict)
    for c in json.loads((ROOT / 'docs' / 'live_schema_columns.json').read_text()):
        out[c['table']][c['column']] = dict(type=c['type'], length=c['length'], precision=c['precision'],
                                            scale=c['scale'], nullable=c['nullable'], default=None)
    return dict(out)


LOOKUPS = dict(buyer=[dict(id=1, label='ABC TEXTILES', cancelled=False)], style=[], description=[],
               size=[dict(id=7, label='M', cancelled=False)], color=[], uom=[dict(id=3, label='PCS', cancelled=False)],
               merch=[dict(id=9, label='OLD MERCH', cancelled=True)])


def order(**kw):
    o = workbooks.base_order()
    o.update(order_ref='TEST-1', quantity_basis='order',
             lines=[dict(label='', color='', declared_total=None, sizes=[dict(size='M', quantity=5, source={})])])
    o.update(kw)
    return o


def ops(plan, table):
    return [op for op in plan['operations'] if op['table'] == table]


def test_missing_optional_fields_are_not_errors_and_stay_null(registry):
    plan = db.build_plan(registry, LOOKUPS, [order()])
    assert plan['errors'] == []
    head = ops(plan, 'ORDMAS')[0]['values']
    for col in ('BUYER', 'STYLENO', 'BONO', 'ODATE', 'UOM', 'SAMPLETYPE', 'MERCH', 'REMARKS', 'ORDCATEGORY'):
        assert col not in head  # nothing invented: column left NULL
    line = ops(plan, 'ORDSTYLE')[0]['values']
    assert 'COMBO' not in line and 'SHIPDT' not in line
    assert head['OQTY'] == 5


def test_absent_section_creates_no_child_records(registry):
    o = order(lines=[dict(label='Printing', color='', declared_total=None, sizes=[dict(size='M', quantity=None, source={})])])
    plan = db.build_plan(registry, LOOKUPS, [o])
    assert plan['errors'] == []
    assert ops(plan, 'ORDSTYLE') == [] and ops(plan, 'ORDSIZE') == []
    assert 'OQTY' not in ops(plan, 'ORDMAS')[0]['values']
    assert any('no quantity' in i for i in plan['info'])


def test_real_problems_still_block(registry):
    o = order(order_ref='', order_date='05/06/2026', merch='OLD MERCH',
              lines=[dict(label='', color='', declared_total=9, sizes=[dict(size='', quantity=4, source={'cell': 'B9'}),
                                                                      dict(size='M', quantity=-1, source={})])])
    errors = ' | '.join(db.build_plan(registry, LOOKUPS, [o], existing_refs={'TEST-1'})['errors'])
    assert 'order reference' in errors
    assert 'two different dates' in errors        # 05/06 is never guessed
    assert 'cancelled' in errors
    assert 'has no size name' in errors and 'B9' in errors
    assert 'non-negative whole number' in errors
    assert 'already exists' in db.build_plan(registry, LOOKUPS, [order()], existing_refs={'TEST-1'})['errors'][0]


def test_dates_converted_but_raw_value_kept(registry):
    plan = db.build_plan(registry, LOOKUPS, [order(order_date='15/12/26', ship_date='2026-12-20')])
    head = ops(plan, 'ORDMAS')[0]
    assert head['values']['ODATE'] == '2026-12-15' and head['columns']['ODATE']['raw'] == '15/12/26'
    assert ops(plan, 'ORDSTYLE')[0]['values']['SHIPDT'] == '2026-12-20'


def test_pieces_per_unit_is_optional_and_explicit(registry):
    plain_plan = db.build_plan(registry, LOOKUPS, [order()])
    assert ops(plain_plan, 'ORDSIZE')[0]['values']['RQTY'] == 5
    packed = db.build_plan(registry, LOOKUPS, [order(pieces_per_unit=3)])
    assert ops(packed, 'ORDSIZE')[0]['values']['RQTY'] == 15
    assert ops(packed, 'ORDMAS')[0]['values']['UOM'] == 3


def test_extra_columns_are_typed_and_engine_columns_protected(registry):
    o = order(extras={'REMARKS': dict(value='Rush order', source={'sheet': 'S', 'cell': 'B2'}),
                      'PRICE': dict(value='12.50', source={}), 'CONVRATE': dict(value='heavy', source={}),
                      'OQTY': dict(value=99, source={}), 'NOPE': dict(value=1, source={})})
    plan = db.build_plan(registry, LOOKUPS, [o])
    head = ops(plan, 'ORDMAS')[0]
    assert head['values']['REMARKS'] == 'Rush order' and head['columns']['REMARKS']['source']['cell'] == 'B2'
    assert head['values']['PRICE'] == '12.5'  # NUMBER(11,5); exact decimal kept as text, bound as Decimal
    errors = ' | '.join(plan['errors'])
    assert 'ORDMAS.CONVRATE needs a number' in errors
    assert 'OQTY is filled automatically' in errors
    assert 'NOPE does not exist' in errors
    assert db.build_plan(registry, LOOKUPS, [order(extras={'REMARKS': dict(value='x' * 5000)})])['errors']


def test_plan_hash_changes_with_any_value(registry):
    a = db.build_plan(registry, LOOKUPS, [order()])
    b = db.build_plan(registry, LOOKUPS, [order(po_number='PO-1')])
    assert a['hash'] != b['hash'] and a['hash'] == db.build_plan(registry, LOOKUPS, [order()])['hash']


def test_label_layout_detected_with_provenance():
    data = workbooks.parse(factory.label_layout())
    o = data['orders'][0]
    assert data['format'] == 'Detected labels'
    assert (o['buyer'], o['order_ref'], o['style'], o['ship_date'], o['season']) == ('ABC TEXTILES', 'PK/26-27/S/1320', '0008', '2026-12-15', 'SS27')
    assert o['provenance']['buyer']['cell'] == 'B3'
    assert [(s['size'], s['quantity'], s['source']['cell']) for s in o['lines'][0]['sizes']] == [('M', 2000, 'B10'), ('L', 3000, 'B11')]
    assert o['lines'][0]['color'] == 'NAVY'
    cell = next(c for s in data['sheets'] for c in s['cells'] if c['cell'] == 'B6')
    assert cell['format'] == 'dd-mm-yyyy'


def test_horizontal_sizes_and_uncomputed_formula():
    data = workbooks.parse(factory.horizontal_layout())
    o = data['orders'][0]
    assert o['buyer'] == 'DEMO BUYER' and o['po_number'] == 'PO-7781'
    assert [(s['size'], s['quantity']) for s in o['lines'][0]['sizes']] == [('S', 10), ('M', 20), ('L', 30)]
    total = next(c for c in data['sheets'][0]['cells'] if c['cell'] == 'E5')
    assert total['formula'] == '=SUM(B5:D5)' and total['value'] is None  # never silently calculated
    assert o['lines'][0]['declared_total'] is None


def test_binding_reads_value_from_workbook_and_template_detects_moved_layout():
    data = workbooks.parse(factory.label_layout())
    o = data['orders'][0]; sheet, cell = mapping.find_cell(data['sheets'], 'Buyer Order', 'E3')
    mapping.apply_binding(o, 'column:ORDMAS.REMARKS', sheet, cell)
    assert o['extras']['REMARKS'] == dict(value='SS27', source=dict(sheet='Buyer Order', cell='E3', value='SS27', formula=None, via='Excel cell'))
    with pytest.raises(ValueError):
        mapping.find_cell(data['sheets'], 'Buyer Order', 'Z99')
    with pytest.raises(ValueError):
        mapping.apply_binding(o, 'column:ORDMAS.X;DROP', sheet, cell)
    draft = dict(sheets=data['sheets'], bindings=[dict(order=0, target='field:buyer', sheet='Buyer Order', cell='B3')])
    template = dict(bindings=mapping.template_bindings(draft))
    assert template['bindings'][0]['anchor'] == dict(cell='A3', text='buyer')
    assert mapping.match_template(template, workbooks.parse(factory.label_layout(buyer='XYZ'))['sheets'])['score'] == 1
    moved = mapping.match_template(template, workbooks.parse(factory.moved_layout())['sheets'])
    assert moved['score'] == 0 and moved['missing']


def test_table_design_validation():
    good = dict(name='order_printing', columns=[dict(name='PRINT_ID', type='NUMBER', required=True),
                                                dict(name='ink_type', type='VARCHAR2', length=50),
                                                dict(name='INK_QTY', type='NUMBER', precision=10, scale=2, default='0')],
                primary_key=['PRINT_ID'])
    result = database.table_ddl(good, {'ORDMAS'})
    assert result['errors'] == []
    assert result['ddl'] == ('CREATE TABLE "GARMENT_DEMO"."ORDER_PRINTING" ("PRINT_ID" NUMBER NOT NULL, "INK_TYPE" VARCHAR2(50 CHAR), '
                             '"INK_QTY" NUMBER(10,2) DEFAULT 0, CONSTRAINT "PP_PK_ORDER_PRINTING" PRIMARY KEY ("PRINT_ID"))')
    bad = dict(name='ORDMAS', columns=[dict(name='DATE', type='DATE'), dict(name='x"; drop', type='VARCHAR2', default="a'; --")],
               primary_key=['NOPE'])
    errors = ' | '.join(database.table_ddl(bad, {'ORDMAS'})['errors'])
    for expected in ('already exists', 'reserved word', 'use letters', 'cannot contain', 'not in the table'):
        assert expected in errors
    assert database.has_cycle([dict(parent='A', child='B')], dict(parent='B', child='A'))
