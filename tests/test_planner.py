"""Planner accuracy: registry-driven mapping, business validation, BOM arithmetic, relationships."""
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import pytest
from backend import workbooks
from backend.erp import planner, mapping, catalog
from fixtures import merch_bom

GOLDEN = json.loads((Path(__file__).parent / 'fixtures' / 'plan_golden.json').read_text())
BOM_TABLES = ('ORDFAB', 'ORDYARN', 'ORDACC')


def snapshot(**kw):
    return catalog.SnapshotCatalog(**kw)


def verified_bom(skip_masters=('fabric', 'count', 'accessory')):
    """Admin verification of the BOM tables, leaving columns whose master table is absent unverified."""
    base = mapping.effective(); ov = []
    for t in BOM_TABLES:
        ov.append(dict(table=t, column='', status='verified'))
        for c, spec in base['tables'][t]['columns'].items():
            if spec['status'] == 'proposed' and spec.get('master') not in skip_masters:
                ov.append(dict(table=t, column=c, status='verified'))
    return mapping.effective(ov)


def normalized(p):
    """Golden comparison: generated IDs by what they point at. The golden file predates planner.Ref ('$key' text) and
    blank-to-NULL ('' is now planned as None, as Oracle stores it)."""
    keys = {'$' + op['key']: op for op in p['operations']}
    def lab(v):
        v = '$' + v['ref'] if isinstance(v, dict) and 'ref' in v else None if v == '' else v
        if isinstance(v, str) and v in keys:
            op = keys[v]
            if op['table'].startswith('ORD'):
                return f'{op["table"]}:{op["key"]}'
            return op['table'] + ':' + [x for x in op['values'].values() if not isinstance(x, dict) and not str(x).startswith('$')][-1]
        return v
    return sorted(json.dumps([op['table'], op['action'], op['key'] if op['table'].startswith('ORD') else '',
                              {k: lab(v) for k, v in op['values'].items()}], sort_keys=True, default=str) for op in p['operations'])


@pytest.mark.parametrize('case', sorted(GOLDEN['cases']))
def test_registry_planner_reproduces_verified_order_mapping(case):
    """Moving the ORDMAS/ORDSTYLE/ORDSIZE mapping into the registry changed nothing."""
    roles = {k: v['table'] for k, v in mapping.effective()['masters'].items()}
    cat = snapshot(masters={roles[r]: v for r, v in GOLDEN['masters'].items()}, existing_refs={'EXIST'})
    new = planner.plan(cat, deepcopy(GOLDEN['cases'][case]), 'h' * 64, mapping.effective())
    old = GOLDEN['out'][case]
    assert normalized(new) == normalized(old)
    assert sorted(new['errors']) == sorted(old['errors'])
    assert new['warnings'] == old['warnings']
    assert {k: new['totals'][k] for k in old['totals']} == old['totals']


def order_from_fixture(**changes):
    o = workbooks.parse(merch_bom())['orders'][0]
    o.update(order_ref='TEST/1', order_date='2026-10-17', quantity_basis='order_demo', lines=deepcopy(o['order_lines']))
    o.update(qty_unit='PCS', pieces_per_unit=1)   # §5: this BOM's size total (150) ≠ QTY(IN PIECES) (200), so the unit is asked
    o.update(changes)
    return o


def test_unverified_bom_is_previewed_but_never_planned_for_insert():
    p = planner.plan(snapshot(), [order_from_fixture()], 'f' * 64, mapping.effective())
    assert not p['errors'], p['errors']
    assert not [op for op in p['operations'] if op['table'] in BOM_TABLES]
    assert {r['table'] for r in p['reference_only']} == set(BOM_TABLES)
    assert p['totals']['fabrics'] == 3


def test_fabric_kg_yarn_split_and_relationships_when_verified():
    o = order_from_fixture()
    o['accessories'] = [a for a in o['accessories'] if len(a['description']) <= 50]
    p = planner.plan(snapshot(), [o], 'f' * 64, verified_bom())
    assert not p['errors'], p['errors']
    by = lambda t: [op for op in p['operations'] if op['table'] == t]
    fab = by('ORDFAB'); yarn = by('ORDYARN'); lines = by('ORDSTYLE')
    # 100 pcs × 60 g ÷ 1000 × 1.03 = 6.18 kg (garment 1); garment 2 body 50 × 60 → 3.09; binding 50 × 25 → 1.2875 → 1.288
    assert [f['values']['POQTY'] for f in fab] == [6.18, 3.09, 1.288]
    assert p['totals']['fabric_kg'] == pytest.approx(10.558)
    subnos = {l['values']['SUBNO'] for l in lines}
    assert {f['values']['FSUBNO'] for f in fab} <= subnos                     # fabric → its garment line
    assert [y['values']['YQTY'] for y in yarn] == [f['values']['POQTY'] for f in fab]  # 100% yarn reconciles exactly
    assert {(y['values']['SUB9_FSUBNO'], y['values']['SUB9_YDET']) for y in yarn} == {(f['values']['FSUBNO'], f['values']['YDET']) for f in fab}
    assert all(op['values']['ORDMASID'] == planner.Ref('o1') for op in fab + yarn + by('ORDACC'))
    assert 'FABRIC' not in fab[0]['values']   # fabric master unavailable → column stays unwritten
    acc = by('ORDACC')
    tape = [a for a in acc if a['values']['AUOM'] and 'TWILL' in a['values']['ACCDESC']]
    assert len(tape) == 2
    # Accessory sizes written as the order's own size names (no duplicate size masters)
    size_masters = [op['values']['SIZES'] for op in by('SIZE_MAS')]
    assert sorted(size_masters) == sorted(workbooks_sizes(o))


def workbooks_sizes(o):
    return {s['size'] for l in o['lines'] for s in l['sizes']}


def test_yarn_percent_and_rejection_rules_block_when_verified():
    o = order_from_fixture(accessories=[])
    o['fabrics'][0]['yarns'][0]['percent'] = 90
    o['fabrics'][1]['rejection'] = 3
    p = planner.plan(snapshot(), [o], 'f' * 64, verified_bom())
    assert any('total 90%' in e for e in p['errors'])
    assert any('between 1 and 2' in e for e in p['errors'])
    paths = {i['path'] for i in p['issues'] if i['severity'] == 'error'}
    assert 'orders.0.fabrics.0.yarns' in paths and 'orders.0.fabrics.1.rejection' in paths


def test_composition_mismatch_and_workbook_arithmetic_warnings():
    o = order_from_fixture(accessories=[])
    o['fabrics'][0]['yarns'].append(dict(count='140/70 DENIER NYLON LYCRA', color='GREIGE', percent=10))
    o['fabrics'][0]['yarns'][0]['percent'] = 90
    o['fabrics'][0]['total'] = 99
    p = planner.plan(snapshot(), [o], 'f' * 64, verified_bom())
    assert any('includes lycra, nylon' in w for w in p['warnings'])
    assert any('workbook total 99' in w for w in p['warnings'])
    kg = [op['values']['YQTY'] for op in p['operations'] if op['table'] == 'ORDYARN'][:2]
    assert sum(kg) == pytest.approx(6.18)  # residual assigned so the split reconciles exactly


def test_accessory_quantities_scale_to_saved_pieces_and_too_long_text_is_grouped():
    o = order_from_fixture()
    for l in o['lines']:
        for s in l['sizes']:
            s['quantity'] = s['quantity'] // 10
    o['quantity_basis'] = 'sample'
    p = planner.plan(snapshot(), [o], 'f' * 64, verified_bom())
    long = [e for e in p['errors'] if 'at most 50 characters' in e]
    assert len(long) == 1 and 'A VERY LONG ACCESSORY NAME' in long[0]
    assert 'Accessory description / code can hold at most 50 characters in Axpert' in long[0] and '(Oracle ORDACC.ACCDESC)' in long[0]
    assert [i for i in p['issues'] if 'at most 50' in i['message']][0]['path'] == 'orders.0.accessories.6.description'
    assert any('scaled' in w for w in p['warnings'])
    saved = sum(s['quantity'] for l in o['lines'] for s in l['sizes'])
    care = [op for op in p['operations'] if op['table'] == 'ORDACC'][0]
    assert care['values']['APOQTY'] == pytest.approx(round(15.45 * saved / 150, 3))


def test_verified_column_with_missing_master_table_fails_closed():
    m = verified_bom(skip_masters=())
    p = planner.plan(snapshot(), [order_from_fixture(accessories=[])], 'f' * 64, m)
    assert any('FABMAS' in e and 'not available' in e for e in p['errors'])


def test_missing_insert_grant_is_reported():
    p = planner.plan(snapshot(insertable={'ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'BUYMAS', 'STYLENUM', 'STYLEMAS', 'SIZE_MAS', 'COLORMAS', 'UNITMAS', 'MERCHMAS'}),
                     [order_from_fixture(accessories=[])], 'f' * 64, verified_bom())
    assert any('ORDFAB: POPPYS AI has no INSERT permission' in e for e in p['errors'])


def test_duplicate_file_and_existing_order_reference_are_blocked():
    o = order_from_fixture(order_ref='pk/1')
    p = planner.plan(snapshot(existing_refs={'PK/1'}, committed_hashes={'f' * 64}), [o], 'f' * 64, mapping.effective())
    assert any('already been saved to Oracle. Open Saved orders' in e for e in p['errors'])
    assert any('already exists' in e for e in p['errors'])


def test_plan_hash_changes_when_mapping_changes():
    o = order_from_fixture(accessories=[])
    a = planner.plan(snapshot(), [o], 'f' * 64, mapping.effective())
    b = planner.plan(snapshot(), [deepcopy(o)], 'f' * 64, verified_bom())
    assert a['hash'] != b['hash'] and a['mapping_fingerprint'] != b['mapping_fingerprint']


def test_mapping_overrides_cannot_unlock_keys_or_parents():
    m = mapping.effective([dict(table='ORDFAB', column='ORDMASID', status='proposed')])
    assert m['tables']['ORDFAB']['columns']['ORDMASID']['status'] == 'verified'


def test_semantic_model_lists_entities_and_scoped_joins():
    from backend.erp import semantic
    sm = semantic.model(mapping.effective(), snapshot().columns(mapping.referenced_tables(mapping.effective())))
    tables = {e['table']: e for e in sm['entities']}
    assert tables['ORDMAS']['grain'] == 'order' and tables['FABMAS']['status'] == 'unavailable'
    join = next(j for j in sm['joins'] if j['left'] == 'ORDSTYLE' and j['right'] == 'ORDSIZE')
    assert 'ORDSTYLE.ORDMASID = ORDSIZE.ORDMASID' in join['condition']
    assert any(j['right'] == 'BUYMAS' and j['verified'] for j in sm['joins'])


# --- review findings: numbers, packs, table status ------------------------------------------------------------
@pytest.mark.parametrize('value,whole', [('2.000', '2000'), ('12.000', '12000'), ('1.500', '1500'), ('1,500', '1500'), ('1.000.000', '1000000')])
def test_grouped_whole_numbers_are_never_guessed_whatever_reader_kept_them(value, whole):
    errors = []
    assert planner.count(value, 'Order 1, line 1, size M', errors) == 0
    assert errors == [f'Order 1, line 1, size M: “{value}” could mean {whole} — type the quantity as a whole number.']
    assert planner.count('2.0', 'x', []) == 2 and planner.count(2000, 'x', []) == 2000 and planner.count(' 12 ', 'x', []) == 12


def test_a_matrix_cell_written_with_a_dot_between_thousands_is_refused():
    o = order_from_fixture(accessories=[], fabrics=[])
    o['lines'][0]['sizes'][1]['quantity'] = '2.000'           # as a reader keeps it, without the "unclear" marker
    o['lines'][0]['declared_total'] = '10.000'
    p = planner.plan(snapshot(), [o], 'f' * 64, mapping.effective())
    bad = {i['path']: i['message'] for i in p['issues'] if i['severity'] == 'error'}
    assert '“2.000” could mean 2000' in bad['orders.0.lines.0.sizes.1.quantity'] and '“10.000” could mean 10000' in bad['orders.0.lines.0']


def test_decimal_removes_only_commas_that_separate_thousands():
    assert planner.decimal('1,234.5', 'x', []) == Decimal('1234.5') and planner.decimal('1,20,000', 'x', []) == 120000
    assert planner.decimal('180.5', 'x', []) == Decimal('180.5')
    for value in ('180,5', '1,03', '12,34,5', '1,2'):
        errors = []
        assert planner.decimal(value, 'Order 1, fabric 1 consumption', errors) is None
        assert errors == [f'Order 1, fabric 1 consumption: “{value}” has a comma that does not separate thousands — type it with a '
                          'decimal point (for example 180.5).']


@pytest.mark.parametrize('basis', ['order_demo', 'sample'])
def test_pieces_per_pack_must_agree_with_the_workbook_header(basis):
    """Header: 100 packs = 200 pieces, so a pack is 2 pieces whether sample or order quantities are saved."""
    for unit, per, refused in (('PACK', 7, True), ('PACK', 3, True), ('PACK', 2, False), ('PCS', 1, False)):
        o = order_from_fixture(qty_unit=unit, pieces_per_unit=per, accessories=[], fabrics=[], quantity_basis=basis)
        p = planner.plan(snapshot(), [o], 'f' * 64, mapping.effective())
        found = [i for i in p['issues'] if i['severity'] == 'error' and 'pieces per pack' in i['message']]
        assert bool(found) is refused, (unit, per, found)
        if refused:
            assert found[0]['path'] == 'orders.0.pieces_per_unit'
            assert f'you chose {per} pieces per pack, but the workbook header says 100 packs = 200 pieces (2 per pack)' in found[0]['message']


def test_pack_orders_do_not_multiply_trims_and_say_how_fabric_is_worked_out():
    pcs, pack = (order_from_fixture(qty_unit=u, pieces_per_unit=n) for u, n in (('PCS', 1), ('PACK', 2)))
    for o in (pcs, pack):
        o['accessories'] = [a for a in o['accessories'] if len(a['description']) <= 50]
    a, b = (planner.plan(snapshot(), [o], 'f' * 64, verified_bom()) for o in (pcs, pack))
    acc = lambda p: [op['values']['APOQTY'] for op in p['operations'] if op['table'] == 'ORDACC']
    assert acc(a) == acc(b) and not [w for w in b['warnings'] if 'scaled' in w]      # the same order: the workbook's trims
    assert any('packs of 2, so fabric is worked out for the pieces being saved' in w and '12.36 kg, not 6.18 kg' in w for w in b['warnings'])
    for l in pack['lines']:                                    # sample quantities: a tenth of the order, in packs
        for s in l['sizes']:
            s['quantity'] //= 10
    pack['quantity_basis'] = 'sample'
    p = planner.plan(snapshot(), [pack], 'f' * 64, verified_bom())
    pieces = 2 * sum(s['quantity'] for l in pack['lines'] for s in l['sizes'])
    assert acc(p)[0] == pytest.approx(round(15.45 * pieces / 300, 3))      # of the workbook's 150 packs × 2 pieces
    assert any(f'scaled from the workbook’s 150 packs (300 pieces) to the {pieces} pieces being saved' in w for w in p['warnings'])


def test_order_tables_must_be_verified_to_be_saved():
    m = mapping.effective([dict(table='ORDSIZE', column='', status='proposed')])
    p = planner.plan(snapshot(), [order_from_fixture(accessories=[], fabrics=[])], 'f' * 64, m)
    assert any('size quantities (ORDSIZE) is not verified for saving' in e for e in p['errors'])
    assert all(mapping.effective()['tables'][t]['status'] == 'verified' for t in ('ORDMAS', 'ORDSTYLE', 'ORDSIZE'))
    assert not planner.plan(snapshot(), [order_from_fixture(accessories=[], fabrics=[])], 'f' * 64, mapping.effective())['errors']
