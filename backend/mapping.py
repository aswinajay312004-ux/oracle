"""Excel cell → Oracle field mapping: field catalogue, label detection, cell bindings and
buyer templates. Every bound value is read from the stored workbook cells on the server,
so a value and its source reference can never disagree and nothing is invented."""
from __future__ import annotations
from datetime import date, datetime
import re
from .workbooks import ALIASES, base_order, norm, source, text

# Business fields with a verified Oracle destination (see oracle_store.build_plan).
ORDER_FIELDS = {
    'order_ref': dict(label='Order reference', table='ORDMAS', column='DOCID', required=True),
    'buyer': dict(label='Buyer', table='ORDMAS', column='BUYER', master='BUYMAS'),
    'style': dict(label='Style number', table='ORDMAS', column='STYLENO', master='STYLENUM'),
    'description': dict(label='Garment description', table='ORDMAS', column='STYLEDESC', master='STYLEMAS'),
    'po_number': dict(label='Buyer PO / contract', table='ORDMAS', column='BONO'),
    'order_date': dict(label='Order date', table='ORDMAS', column='ODATE', kind='date'),
    'ship_date': dict(label='Ship date', table='ORDSTYLE', column='SHIPDT', kind='date'),
    'merch': dict(label='Merchandiser', table='ORDMAS', column='MERCH', master='MERCHMAS'),
    'sample_type': dict(label='Sample type', table='ORDMAS', column='SAMPLETYPE'),
    'season': dict(label='Season', table=None, column=None),
}
LINE_FIELDS = {
    'color': dict(label='Colour / combo', table='ORDSTYLE', column='COMBO', master='COLORMAS'),
    'label': dict(label='Garment / component', table=None, column=None),
}
SIZE_FIELDS = {
    'size': dict(label='Size', table='ORDSIZE', column='GSIZE', master='SIZE_MAS'),
    'quantity': dict(label='Quantity', table='ORDSIZE', column='RQTY'),
}
# Extra columns a reviewer may bind directly. Columns written by the engine are excluded.
EXTRA_TABLES = ('ORDMAS', 'ORDSTYLE')
LABEL_ALIASES = dict(ALIASES, order_ref=ALIASES['order_ref'] + ['order no', 'order number', 'oc number', 'ref no'],
                     order_date=['order date', 'po date', 'confirmation date', 'oc date'],
                     merch=['merchandiser', 'merch'], season=['season'], sample_type=['sample type'])
SIZE_TOKEN = re.compile(r'^(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|\d{1,2}(-\d{1,2})?\s?(Y|YRS|M|MTHS)?|\d{2,3})$', re.I)


def label_value(cell_value, aliases):
    """`Buyer: ABC` in one cell → ('ABC'). Returns None when the label is not a prefix."""
    raw = text(cell_value)
    if ':' not in raw:
        return None
    head, _, tail = raw.partition(':')
    return tail.strip() if norm(head) in aliases and tail.strip() else None


def neighbour(cells, row, col):
    """The value cell beside (or below) a label cell; never more than three columns away."""
    for c in range(col + 1, col + 4):
        if (row, c) in cells:
            return cells[(row, c)]
    return cells.get((row + 1, col))


def anchor(sheet, cell):
    """Nearest text label left of (or above) a bound cell; used to recognise the layout later."""
    cells = {(c['row'], c['col']): c for c in sheet['cells']}
    for r, c in [(cell['row'], x) for x in range(cell['col'] - 1, max(cell['col'] - 4, 0), -1)] + [(cell['row'] - 1, cell['col'])]:
        found = cells.get((r, c))
        if found and isinstance(found['value'], str) and found['value'].strip():
            return dict(cell=found['cell'], text=norm(found['value']))
    return None


def detect_generic(sheets):
    """Deterministic detection for unknown layouts: `Label | value` pairs and a size table.
    Returns one order with provenance, or None. Confidence is reported per field."""
    for sheet in (s for s in sheets if not s['hidden']):
        cells = {(c['row'], c['col']): c for c in sheet['cells']}
        o = base_order(); found = {}
        for c in sheet['cells']:
            if not isinstance(c['value'], str):
                continue
            for key, aliases in LABEL_ALIASES.items():
                if key in ('size', 'quantity', 'color') or key in found:
                    continue
                names = [norm(a) for a in aliases]
                inline = label_value(c['value'], names)
                if inline is not None:
                    found[key] = (inline, source(sheet['name'], c['cell'], c['value'], c['formula']), 'label in the same cell')
                elif norm(c['value']) in names:
                    v = neighbour(cells, c['row'], c['col'])
                    if v and v['value'] is not None and not (isinstance(v['value'], str) and norm(v['value']) in
                                                            [norm(a) for al in LABEL_ALIASES.values() for a in al]):
                        found[key] = (v['value'], source(sheet['name'], v['cell'], v['value'], v['formula']),
                                      f'value beside label "{text(c["value"])}"')
        lines = size_table(sheet, cells)
        if not found and not lines:
            continue
        for key, (value, src, reason) in found.items():
            o[key] = value.isoformat()[:10] if isinstance(value, (date, datetime)) else text(value)
            o['provenance'][key] = dict(src, reason=reason)
        o['lines'] = lines; o['order_lines'] = [dict(l) for l in lines]; o['quantity_basis'] = 'order'
        o['warnings'] = ['Layout recognised from labels. Check each value and its Excel cell before approving.']
        return o
    return None


def size_table(sheet, cells):
    """Vertical (Size | Qty [| Colour]) or horizontal (sizes across, quantities below) blocks."""
    size_names = [norm(a) for a in ALIASES['size']]; qty_names = [norm(a) for a in ALIASES['quantity']]
    color_names = [norm(a) for a in ALIASES['color']]
    for (r, c), cell in sorted(cells.items()):
        if norm(cell['value']) not in size_names:
            continue
        qcol = next((x for x in range(c + 1, c + 6) if norm(cells.get((r, x), {}).get('value')) in qty_names), None)
        if qcol:  # vertical
            ccol = next((x for x in range(max(c - 3, 1), c + 6) if norm(cells.get((r, x), {}).get('value')) in color_names), None)
            lines = {}
            for rr in range(r + 1, r + 200):
                s, q = cells.get((rr, c)), cells.get((rr, qcol))
                if not s and not q:
                    break
                if norm((s or {}).get('value')) in ('total', 'grand total'):
                    break
                color = text(cells.get((rr, ccol), {}).get('value')) if ccol else ''
                line = lines.setdefault(color, dict(label='', color=color, declared_total=None, provenance={}, sizes=[]))
                if ccol and (rr, ccol) in cells:
                    cc = cells[(rr, ccol)]; line['provenance']['color'] = source(sheet['name'], cc['cell'], cc['value'], cc['formula'])
                line['sizes'].append(dict(size=text((s or {}).get('value')), quantity=(q or {}).get('value'),
                    source=source(sheet['name'], q['cell'], q['value'], q['formula']) if q else {},
                    size_source=source(sheet['name'], s['cell'], s['value'], s['formula']) if s else {}))
            return list(lines.values())
        # horizontal: sizes to the right on this row, quantities on the next row
        cols = [x for x in range(c + 1, c + 60) if (r, x) in cells and SIZE_TOKEN.match(text(cells[(r, x)]['value']))]
        if len(cols) >= 2 and any((r + 1, x) in cells for x in cols):
            total_col = next((x for x in range(cols[-1] + 1, cols[-1] + 3) if norm(cells.get((r, x), {}).get('value')) == 'total'), None)
            line = dict(label='', color='', provenance={},
                        declared_total=cells.get((r + 1, total_col), {}).get('value') if total_col else None,
                        sizes=[dict(size=text(cells[(r, x)]['value']), quantity=cells.get((r + 1, x), {}).get('value'),
                                    source=source(sheet['name'], cells[(r + 1, x)]['cell'], cells[(r + 1, x)]['value'], cells[(r + 1, x)]['formula']) if (r + 1, x) in cells else {},
                                    size_source=source(sheet['name'], cells[(r, x)]['cell'], cells[(r, x)]['value'], cells[(r, x)]['formula'])) for x in cols])
            return [line]
    return []


# ---------------------------------------------------------------- bindings

TARGET = re.compile(r'^(field:(?P<field>\w+)|column:(?P<table>ORDMAS|ORDSTYLE)\.(?P<column>[A-Z][A-Z0-9_$#]{0,127})'
                    r'|line:(?P<line>\d+):(?P<lfield>color|label)|size:(?P<sline>\d+):(?P<size>\d+):(?P<sfield>size|quantity))$')


def find_cell(sheets, sheet_name, address):
    sheet = next((s for s in sheets if s['name'] == sheet_name), None)
    if not sheet:
        raise ValueError('Select a worksheet cell.')
    cell = next((c for c in sheet['cells'] if c['cell'] == address), None)
    if not cell:
        raise ValueError(f'Cell {address} is empty. Choose a cell that contains the value.')
    return sheet, cell


def apply_binding(order, target, sheet, cell, line_column=None, via='Excel cell'):
    """Set one target from a workbook cell (or clear it when cell is None)."""
    m = TARGET.match(target or '')
    if not m:
        raise ValueError('Unknown mapping target.')
    src = dict(source(sheet['name'], cell['cell'], cell['value'], cell['formula']), via=via) if cell else None
    value = None if cell is None else cell['value']
    shown = None if value is None else (value.isoformat()[:10] if isinstance(value, (date, datetime)) else value)
    if m['field']:
        if m['field'] not in ORDER_FIELDS:
            raise ValueError('Unknown order field.')
        order[m['field']] = '' if shown is None else text(shown)
        if src: order['provenance'][m['field']] = src
        else: order['provenance'].pop(m['field'], None)
    elif m['table']:
        extras = order.setdefault('extras', {}) if m['table'] == 'ORDMAS' else None
        if m['table'] == 'ORDSTYLE':
            if line_column is None or not 0 <= line_column < len(order['lines']):
                raise ValueError('Choose the garment line for this column.')
            extras = order['lines'][line_column].setdefault('extras', {})
        if cell is None: extras.pop(m['column'], None)
        else: extras[m['column']] = dict(value=shown, source=src)
    elif m['line'] is not None:
        line = order['lines'][int(m['line'])]
        line[m['lfield']] = '' if shown is None else text(shown)
        if src: line.setdefault('provenance', {})[m['lfield']] = src
    else:
        line = order['lines'][int(m['sline'])]; size = line['sizes'][int(m['size'])]
        if m['sfield'] == 'size':
            size['size'] = '' if shown is None else text(shown); size['size_source'] = src or {}
        else:
            size['quantity'] = shown; size['source'] = src or {}
            # A rebound quantity can no longer be compared with the workbook's own total.
            line.setdefault('original_total', line.get('declared_total')); line['declared_total'] = None
    return order


def template_bindings(draft):
    """Bindings made by the reviewer, with layout anchors, ready to save as a template."""
    sheets = {s['name']: s for s in draft['sheets']}
    result = []
    for b in draft.get('bindings', []):
        sheet = sheets.get(b['sheet'])
        cell = next((c for c in sheet['cells'] if c['cell'] == b['cell']), None) if sheet else None
        if cell:
            result.append(dict(b, anchor=anchor(sheet, cell)))
    return result


def match_template(template, sheets):
    """Score a saved template against a new workbook. A binding only counts when its
    sheet exists and the anchor label beside the cell still reads the same."""
    by_name = {s['name']: s for s in sheets}
    matched, missing = [], []
    for b in template['bindings']:
        sheet = by_name.get(b['sheet'])
        cell = next((c for c in sheet['cells'] if c['cell'] == b['cell']), None) if sheet else None
        now = anchor(sheet, cell) if cell else None
        ok = cell is not None and (b.get('anchor') is None or (now is not None and now == b['anchor']))
        (matched if ok else missing).append(b)
    total = len(template['bindings']) or 1
    return dict(score=len(matched) / total, matched=matched, missing=missing)
