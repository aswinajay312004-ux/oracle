"""Every accepted upload format yields the same order. Files written by other programs (Excel via the
calamine samples, LibreOffice) are checked against our own fixtures so readers are not only tested
against themselves."""
from pathlib import Path
import json
import pytest
from backend import readers, workbooks
from fixtures import merch_bom, flat_orders, as_xls, as_xml2003, as_html, as_csv, as_json_records

FORMATS = Path(__file__).parent / 'fixtures' / 'formats'


def _ws(v):
    return ' '.join(v.split()) if isinstance(v, str) else v


def summary(parsed, cells=True):
    return [(o['buyer'], o['style'], o['description'], o['po_number'], o['ship_date'], o['merch'], o['season'],
             [(l['label'], l['color'], [(x['size'], x['quantity']) + ((x['source']['cell'],) if cells else ()) for x in l['sizes']])
              for l in o['order_lines'] or o['lines']],
             [(_ws(f['component']), f['total']) for f in o['fabrics']], len(o['accessories']), (o.get('packs') or {}).get('pieces'))
            for o in parsed['orders']]


def test_calamine_reference_workbook_reads_identically_in_four_formats():
    def values(path):
        _, sheets = readers.read(path.read_bytes(), path.name)
        return {s['name']: sorted((c['row'], c['col'], c['value']) for c in s['cells']) for s in sheets if s['name'] in ('datatypes', 'issue2', 'issue5')}
    base = values(FORMATS / 'calamine_issues.xlsx')
    assert base['issue2'] == [(1, 1, 1), (1, 2, 'a'), (2, 1, 2), (2, 2, 'b'), (3, 1, 3), (3, 2, 'c')]
    for ext in ('xls', 'xlsb', 'ods'):
        assert values(FORMATS / f'calamine_issues.{ext}') == base, ext


@pytest.mark.parametrize('name,data,cells', [
    ('bom.xls', lambda: as_xls(merch_bom()), True),
    ('bom_lo.xls', lambda: (FORMATS / 'merch_bom_libreoffice.xls').read_bytes(), True),
    ('bom_lo.ods', lambda: (FORMATS / 'merch_bom_libreoffice.ods').read_bytes(), True),
    ('bom.xml', lambda: as_xml2003(merch_bom()), True),
    ('bom_export.xls', lambda: as_html(merch_bom()), True),
    ('bom_lo.html', lambda: (FORMATS / 'merch_bom_libreoffice.html').read_bytes(), False),
])
def test_merch_bom_gives_the_same_order_in_every_format(name, data, cells):
    expected = summary(workbooks.parse(merch_bom()), cells)
    got = workbooks.parse(data(), filename=name)
    assert got['format'] == 'Merch BOM'
    assert summary(got, cells) == expected


@pytest.mark.parametrize('name,data', [
    ('orders.csv', lambda: as_csv(flat_orders())),
    ('orders_semicolon.csv', lambda: as_csv(flat_orders(), ';')),
    ('orders.txt', lambda: as_csv(flat_orders(), '\t', 'utf-16')),          # Excel "Unicode text"
    ('orders.tsv', lambda: as_csv(flat_orders(), '\t', 'utf-8-sig')),
    ('orders.json', lambda: as_json_records(flat_orders())),
    ('orders.xls', lambda: as_html(flat_orders())),
])
def test_flat_orders_give_the_same_lines_in_every_text_format(name, data):
    expected = summary(workbooks.parse(flat_orders()), cells=False)
    got = workbooks.parse(data(), filename=name)
    assert summary(got, cells=False) == expected
    assert got['orders'][0]['style'] == '0008'          # leading-zero style code survives text formats


def test_json_document_and_sheet_map_shapes():
    doc = {'Buyer': 'ZEN KIDS', 'Style No': 'ZK-1', 'PO No': 'ZK/PO/1',
           'lines': [{'Style No': 'ZK-1', 'Colour': 'NAVY', 'Size': 'S', 'Qty': 10}, {'Style No': 'ZK-1', 'Colour': 'NAVY', 'Size': 'M', 'Qty': 12}]}
    o = workbooks.parse(json.dumps(doc).encode(), filename='po.json')['orders'][0]
    assert (o['buyer'], o['style'], o['po_number']) == ('ZEN KIDS', 'ZK-1', 'ZK/PO/1')
    assert [(s['size'], s['quantity']) for s in o['lines'][0]['sizes']] == [('S', 10), ('M', 12)]
    sheets = {'Orders': [['Style', 'Size', 'Qty'], ['A1', 'S', 5]], 'Notes': [{'note': 'x'}]}
    _, out = readers.read(json.dumps(sheets).encode(), 'm.json')
    assert [s['name'] for s in out] == ['Orders', 'Notes']
    own = {'sheets': [{'name': 'PO', 'cells': [{'cell': 'B2', 'value': 'NEXT'}, {'row': 3, 'col': 1, 'value': 7}]}]}
    _, out = readers.read(json.dumps(own).encode(), 'own.json')
    assert {c['cell']: c['value'] for c in out[0]['cells']} == {'B2': 'NEXT', 'A3': 7}


@pytest.mark.parametrize('name,content,reason', [
    ('empty.xlsx', b'', 'empty'),
    ('scan.pdf.xlsx', b'%PDF-1.7 binary\x00\x01', 'not an Excel workbook'),
    ('random.xls', bytes(range(256)) * 4, 'not an Excel workbook'),
    ('notes.xml', b'<?xml version="1.0"?><note>hi</note>', 'no spreadsheet table'),
    ('archive.xlsx', b'PK\x03\x04garbage', 'damaged'),
])
def test_non_spreadsheets_are_refused_with_a_reason(name, content, reason):
    with pytest.raises(readers.Unsupported, match=reason):
        readers.read(content, name)


def test_hostile_content_is_data_only():
    bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table><Row><Cell><Data>&b;</Data></Cell></Row></Table></Worksheet></Workbook>').encode()
    with pytest.raises(ValueError):
        readers.read(bomb, 'bomb.xml')                          # entity expansion refused (defusedxml)
    deep = ('[' * 40 + ']' * 40).encode()
    with pytest.raises(ValueError, match='nested too deeply'):
        readers.read(deep, 'deep.json')
    _, sheets = readers.read(b'Style,Size,Qty\r\n=cmd|calc,S,=1+1\r\n', 'inject.csv')
    values = {c['cell']: c['value'] for c in sheets[0]['cells']}
    assert values['A2'] == '=cmd|calc' and values['C2'] == '=1+1' and all(c['formula'] is None for c in sheets[0]['cells'])
    html = b'<table><tr><td>Buyer</td><td>NEXT<script>steal()</script></td></tr></table>'
    _, sheets = readers.read(html, 'x.xls')
    assert {c['cell']: c['value'] for c in sheets[0]['cells']} == {'A1': 'Buyer', 'B1': 'NEXT'}


def test_html_spans_become_merged_cells_like_excel():
    html = b'<table><tr><td colspan="2">TITLE</td><td>X</td></tr><tr><td rowspan="2">A</td><td>B</td><td>C</td></tr><tr><td>D</td><td>E</td></tr></table>'
    _, sheets = readers.read(html, 'span.xls')
    s = sheets[0]
    assert {c['cell']: c['value'] for c in s['cells']} == {'A1': 'TITLE', 'C1': 'X', 'A2': 'A', 'B2': 'B', 'C2': 'C', 'B3': 'D', 'C3': 'E'}
    assert s['merged'] == ['A1:B1', 'A2:A3']


def test_buyer_dates_become_iso_and_ambiguity_is_reported():
    cases = {'2027-01-15': ('2027-01-15', None), '2027-01-15 0:00:00': ('2027-01-15', None), '20/12/2026': ('2026-12-20', None),
             '20-Dec-26': ('2026-12-20', None), '5 Mar 2027': ('2027-03-05', None), '2026/12/20': ('2026-12-20', None),
             '12/20/2026': ('2026-12-20', '"12/20/2026" read as month/day/year'), 46402: ('2027-01-15', 'Excel date number 46402 read as a date'),
             'ASAP': (None, None), '31/02/2026': (None, None)}
    for raw, expected in cases.items():
        assert workbooks.iso_date(raw) == expected, raw
    assert workbooks.iso_date('03/04/2026') == ('2026-04-03', '"03/04/2026" read as day/month/year')


def test_typed_text_values_follow_excel_display_rules():
    assert [readers.typed(v) for v in ('1,500', '0008', '12', '-3', '2.50', '0.5', ' 7 ', 'N/A', '')] == \
        [1500, '0008', 12, -3, 2.5, 0.5, 7, 'N/A', None]
