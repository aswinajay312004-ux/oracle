"""Document understanding on synthetic layouts (structure of the client files, invented values)."""
from backend import workbooks, ai
from fixtures import merch_bom, flat_orders, unknown_matrix, as_xls


def test_merch_bom_header_blocks_bom_and_provenance():
    d = workbooks.parse(merch_bom())
    assert d['format'] == 'Merch BOM'
    o = d['orders'][0]
    assert (o['buyer'], o['style'], o['po_number'], o['season'], o['merch'], o['ship_date']) == \
        ('TESTBUYER', 'T1001', 'TC100-1', 'SS-27', 'TESTMERCH', '2027-01-15')
    assert o['order_ref'] == ''                      # OCN heading is not a value
    assert o['provenance']['style']['cell'] == 'C4'
    assert sum(s['quantity'] for l in o['order_lines'] for s in l['sizes']) == 150
    # the sample block is all zero: the buyer order quantities are shown (summary not added), the choice is asked
    assert sum(s['quantity'] for l in o['lines'] for s in l['sizes']) == 150 and o['quantity_basis'] == 'unconfirmed'
    assert o['warnings'][0].startswith('The sample section of this file is empty')
    assert o['packs']['packs'] == 100 and o['packs']['pieces'] == 200 and o['components'] == 2 and o['packing_type'] == '2PC PACK'
    assert [f['line'] for f in o['fabrics']] == [1, 2, 2]
    assert [f['portion'] for f in o['fabrics']] == ['BODY', 'BODY', 'BINDING']
    assert o['fabrics'][0]['yarns'][0]['percent'] == 100.0
    cats = [a['category'] for a in o['accessories']]
    assert cats.count('CARE LABEL') == 4 and cats.count('TWILL TAPE 5MM') == 2
    tape = [a for a in o['accessories'] if a['category'] == 'TWILL TAPE 5MM']
    assert [a['line'] for a in tape] == [1, 2] and {a['uom'] for a in tape} == {'MTR'}
    assert all(a['category'] != '' for a in o['accessories'])


def test_unlabelled_subtotal_rows_are_not_accessories_and_duplicates_warned():
    o = workbooks.parse(merch_bom(extra_sewing_block=True))['orders'][0]
    assert all(a['total'] != 9.27 for a in o['accessories'])
    assert any('appears in two tables' in w for w in o['warnings'])


def test_flat_table_keeps_leading_zero_style():
    o = workbooks.parse(flat_orders())['orders'][0]
    assert o['style'] == '0008' and o['buyer'] == 'BUYER X'
    assert [(l['color'], len(l['sizes'])) for l in o['lines']] == [('BLUE', 2), ('RED', 1)]


def test_unknown_layout_gets_heuristic_mapping_then_template_auto_applies():
    content = unknown_matrix()
    d = workbooks.parse(content)
    assert d['orders'] == [] and d['format'] == 'Needs column mapping'
    sheet = d['sheets'][0]
    definition = ai.heuristic(sheet)
    assert definition['table']['type'] == 'matrix' and definition['table']['header_row'] == 5
    assert definition['fields']['buyer'] == 'B1' and definition['fields']['style'] == 'B2'
    orders = workbooks.apply_template(d['sheets'], sheet, definition)
    assert orders[0]['buyer'] == 'ZEN KIDS' and orders[0]['po_number'] == 'ZK/PO/9'
    assert [sum(s['quantity'] for s in l['sizes']) for l in orders[0]['lines']] == [60, 15]
    template = dict(id='t1', name='Zen', signature=workbooks.signature(sheet), labels=sorted(workbooks.labels(sheet)), definition=definition)
    again = workbooks.parse(content, [template])
    assert again['format'] == 'Template: Zen' and again['templates_used'] == ['t1']
    assert again['orders'][0]['style'] == 'ZK-55'


def test_template_definitions_are_validated():
    sheet = workbooks.parse(unknown_matrix())['sheets'][0]
    import pytest
    with pytest.raises(ValueError):
        workbooks.validate_definition(sheet, dict(fields={'sql': 'A1'}, table=dict(type='matrix', header_row=5, first_size_col=3, last_size_col=5)))
    with pytest.raises(ValueError):
        workbooks.validate_definition(sheet, dict(fields={}, table=dict(type='matrix', header_row=5, first_size_col=5, last_size_col=3)))


def _summary(parsed):
    return [(o['buyer'], o['style'], o['po_number'], o['ship_date'],
             [(l['color'], [(x['size'], x['quantity'], x['source']['cell']) for x in l['sizes']]) for l in o['lines']],
             len(o['fabrics']), len(o['accessories']), o['provenance'].get('buyer', {}).get('cell')) for o in parsed['orders']]


def test_legacy_xls_is_read_exactly_like_xlsx():
    for build in (merch_bom, flat_orders, unknown_matrix):
        xlsx = build(); xls = as_xls(xlsx)
        assert workbooks.kind_of(xls) == 'xls'
        a, b = workbooks.parse(xlsx), workbooks.parse(xls)
        assert a['format'] == b['format'] and _summary(a) == _summary(b), build.__name__


def test_xls_keeps_leading_zero_style_codes():
    o = workbooks.parse(as_xls(flat_orders()))['orders'][0]
    assert o['style'] == '0008'


def test_only_excel_files_are_accepted():
    import pytest
    for content in (b'%PDF-1.7 not excel', b'plain text', b''):
        with pytest.raises(ValueError, match='not an Excel workbook|empty'):
            workbooks.inspect(content)
