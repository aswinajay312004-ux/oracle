"""Read-only spreadsheet ingestion (every Excel format and JSON, via backend/readers.py). Cell text is data,
never instructions.

Pipeline per sheet (docs/EXCEL_UNDERSTANDING.md): saved buyer template (layout match + unchanged anchor
labels + validator) → built-in structural detectors (merch BOM, buyer contract) → alias-matched flat table
(style as a column or a header field) → "needs mapping" (backend/matcher.py proposes).
BOM tables (fabric, accessories) are detected by their header structure on the same sheet.
The effective business fields (backend/fields.py: defaults + admin aliases) are passed in explicitly.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime
from io import BytesIO
import hashlib
import re
from . import readers, fields as F

# Compatibility names: the defaults now live in backend/fields.py (parsing uses the effective fields passed in).
ALIASES = F.DEFAULT.alias_map()
HEADER_FIELDS = F.DEFAULT.header_keys
FABRIC_COLUMNS = {
 'component': ['garment', 'garment portion', 'portion', 'component'],
 'color': ['colour description', 'color description', 'colour', 'color'],
 'base_color': ['base colour', 'base color'],
 'quality': ['fabric quality', 'fabric', 'quality', 'fabric type'],
 'gsm': ['gsm'], 'content': ['content', 'composition'], 'count': ['count', 'yarn count'],
 'dia': ['dia', 'width'], 'order_qty': ['order qty', 'qty'], 'yy': ['yy', 'consumption', 'grams per pc', 'avg weight'],
 'requirement': ['requirment', 'requirement', 'req kg', 'required kg'], 'rejection': ['rej', 'rejection', 'wastage'],
 'total': ['total qty', 'total kg', 'total'],
}
ACC_COLUMNS = {
 'first': ['garment', 'matierial', 'material', 'item', 'accessory', 'trim'],
 'code': ['code', 'shade', 'article'], 'color': ['color', 'colour', 'thread color', 'thread colour'], 'size': ['size'],
 'supplier': ['supplier', 'vendor'], 'quality': ['quality'],
 'order_qty': ['order qty'], 'yy': ['yy', 'yy multi', 'consumption'], 'required': ['required qty'],
 'rejection': ['rejection', 'rej'], 'total': ['req in single', 'req in mtr', 'req in pcs', 'req in kgs', 'total required', 'req in'],
}
PORTIONS = ('BODY', 'BINDING', 'RIB', 'COLLAR', 'CUFF', 'POCKET', 'HOOD', 'LINING', 'TAPE', 'PIPING')


def norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def scalar(cell):
    value = cell.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, (int, float)) and re.fullmatch(r'0{2,}', cell.number_format):
        return str(int(value)).zfill(len(cell.number_format))
    return value.strip() if isinstance(value, str) else value


def text(value):
    return '' if value is None else str(value).strip()


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(',', '').strip())
    except ValueError:
        return None


def source(sheet, address, value, formula=None):
    return {'sheet': sheet, 'cell': address, 'value': value, 'formula': formula}


def base_order():
    return dict(buyer='', style='', description='', po_number='', order_ref='', ship_date='',
        order_date='', season='', merch='', sample_type='DEVELOPMENT SAMPLE', exporter='', against='',
        department='', gender='', packing_type='', components=None,
        qty_unit='', pieces_per_unit=None, quantity_basis='unconfirmed', lines=[], provenance={},
        warnings=[], order_lines=[], sample_lines=[], fabrics=[], accessories=[], packs=None, workbook_pieces=None)


MAX_BYTES = readers.MAX_BYTES


def kind_of(content, filename=''):
    """The real spreadsheet format of an upload (see backend/readers.py)."""
    return readers.sniff(content, filename)


def inspect(content, filename=''):
    return readers.read(content, filename)[1]


# --- layout signatures (reusable templates) ------------------------------------
def labels(sheet, rows=60):
    """Text labels that describe a layout (not its data): short, non-numeric cells near the top."""
    out = set()
    for c in sheet['cells']:
        v = c['value']
        if c['row'] > rows or not isinstance(v, str) or number(v) is not None or re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
            continue
        n = norm(v)
        if 2 <= len(n) <= 40 and sum(ch.isdigit() for ch in n) <= len(n) // 3:
            out.add(f'{c["row"]}:{c["col"]}:{n}')
    return out


def signature(sheet):
    return signature_of(labels(sheet))


def signature_of(label_set):
    return hashlib.sha256('|'.join(sorted(label_set)).encode()).hexdigest()


def layout_labels(sheet, definition):
    """The labels of a layout without the cells a definition reads as data (header values, size headings,
    everything below the table header), so the next file from the same buyer — new style, PO, sizes,
    colours — still has the same layout signature."""
    d = definition or {}; t = d.get('table') or {}; skip = set()
    for ref in (d.get('fields') or {}).values():
        try:
            skip.add(cell_ref(ref))
        except ValueError:
            pass
    hr = t.get('header_row') if isinstance(t.get('header_row'), int) else 0
    first, last = t.get('first_size_col') or 0, t.get('last_size_col') or 0
    out = set()
    for lab in labels(sheet):
        r, c, _ = lab.split(':', 2); r, c = int(r), int(c)
        if (r, c) in skip or (hr and r > hr) or (t.get('type') == 'matrix' and r == hr and first <= c <= last):
            continue
        out.add(lab)
    return out


def layout_signature(sheet, definition):
    return signature_of(layout_labels(sheet, definition))


def similarity(a, b):
    """Position-aware Jaccard similarity between two label sets."""
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def template_score(sheet, t):
    """1.0 for the same layout signature, otherwise label similarity. Templates saved before anchors
    existed were signed over all short text of the sheet; newer ones over their layout labels only."""
    if t.get('anchors') is None:
        mine = labels(sheet)
    else:
        mine = layout_labels(sheet, t.get('definition'))
    return 1.0 if signature_of(mine) == t['signature'] else similarity(mine, t['labels'])


def match_template(sheet, templates, threshold=0.8):
    best = None
    for t in templates:
        score = template_score(sheet, t)
        if score >= threshold and (best is None or score > best[0]):
            best = (score, t)
    return best


# --- cell helpers ----------------------------------------------------------------
class Grid:
    def __init__(self, sheet, fields=None):
        self.sheet = sheet
        self.fields = F.resolve(fields)
        self.cells = {(c['row'], c['col']): c for c in sheet['cells']}
        self.rows = sheet['rows']; self.cols = sheet['cols']

    def v(self, r, c):
        return self.cells.get((r, c), {}).get('value')

    def p(self, r, c):
        cell = self.cells.get((r, c), {})
        return source(self.sheet['name'], cell.get('cell', ''), cell.get('value'), cell.get('formula'))

    def find(self, aliases, rows=None, first=True):
        names = {norm(a) for a in aliases}
        hits = sorted((c['row'], c['col']) for c in self.sheet['cells']
                      if norm(c['value']) in names and (rows is None or c['row'] in rows))
        return (hits[0] if hits else None) if first else hits

    def right(self, r, c, span=3):
        for cc in range(c + 1, c + span + 1):
            if self.v(r, cc) not in (None, ''):
                return r, cc
        return None

    def below(self, r, c, span=2):
        for rr in range(r + 1, r + span + 1):
            if self.v(rr, c) not in (None, ''):
                return rr, c
        return None

    def header_columns(self, row, spec):
        """Map semantic -> column for one header row, first match wins, prefix aliases allowed."""
        found = {}
        for col in range(1, self.cols + 1):
            n = norm(self.v(row, col))
            if not n:
                continue
            for key, aliases in spec.items():
                if key in found:
                    continue
                if any(n == norm(a) or (len(norm(a)) >= 3 and n.startswith(norm(a) + ' ')) for a in aliases):
                    found[key] = col; break
        return found


def address(col, row):
    label = ''
    while col:
        col, rem = divmod(col - 1, 26); label = chr(65 + rem) + label
    return f'{label}{row}'


def cell_ref(ref):
    m = re.fullmatch(r'\s*([A-Za-z]{1,3})\s*(\d{1,6})\s*', str(ref or ''))
    if not m:
        raise ValueError(f'"{ref}" is not a cell address such as C4.')
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


# --- detectors -----------------------------------------------------------------
OTHER_LABELS = frozenset({'bom date', 'fab ih date', 'destination'})
LABEL_WORDS = F.DEFAULT.label_words | OTHER_LABELS


def looks_like_label(value, fields=None):
    """A neighbour that is itself a heading (e.g. "QTY(IN PACKS)") is not the value of a label."""
    n = norm(value)
    words = F.resolve(fields).label_words
    return isinstance(value, str) and (n in words or n in OTHER_LABELS or bool(re.search(r'\b(qty|quantity|price|total)\b', n)))


def field_value(g, r, c, key):
    """A header value as the order keeps it: dates stay raw for iso_date (an Excel serial number is a
    date, not the text "46402"), whole numbers for number fields, trimmed text otherwise."""
    v = g.v(r, c); kind = g.fields.kind(key)
    if kind == 'date' and isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if kind == 'number':
        n = number(v)
        if n is not None:
            return int(n) if float(n).is_integer() else n
    return text(v)


def header_value(g, key, limit_row):
    """(row, col) of the value next to a label of this field above limit_row, or None."""
    rows = range(1, max(2, limit_row))
    return next((t for hit in g.find(g.fields.aliases(key), rows, first=False) for t in [g.right(*hit)]
                 if t and not looks_like_label(g.v(*t), g.fields)), None)


def header_fields(g, o, limit_row, only_missing=False):
    """Key-value labels ("BUYER | NEXT") anywhere above the first data block."""
    for key in g.fields.header_keys:
        if only_missing and o.get(key) not in (None, ''):
            continue
        target = header_value(g, key, limit_row)
        if target:
            o[key] = field_value(g, *target, key); o['provenance'][key] = g.p(*target)


# --- quantity unit evidence (docs/EXCEL_UNDERSTANDING.md §5) ---------------------------
PCS_WORDS = ('pcs', 'pc', 'pieces', 'piece')
PACK_WORDS = ('pack', 'packs', 'pk', 'set', 'sets', 'ratio')
_UNIT_TOKEN = re.compile(r'(\d*)(pcs|pc|pieces|piece|packs|pack|pk|sets|set|ratio)')
_PER_PACK = re.compile(r'\b(\d{1,3}) ?(?:pcs|pc|pieces|piece)? ?(?:pack|packs|pk|set|sets)\b|\b(?:pack|packs|pk|set|sets) of (\d{1,3})\b')
_QTY_WORDS = re.compile(r'\b(qty|quantity|quantities)\b')


def unit_words(value):
    """('PCS' | 'PACK' | '?' (both) | '', pieces per pack or None) from the words of a header or label."""
    n = norm(value)
    kinds = {('PACK' if m.group(2) in PACK_WORDS else 'PCS') for t in n.split() for m in [_UNIT_TOKEN.fullmatch(t)] if m}
    m = _PER_PACK.search(n)
    if m and 'PACK' in kinds and int(m.group(1) or m.group(2)) >= 2:
        return 'PACK', int(m.group(1) or m.group(2))
    if kinds == {'PCS'}:
        return 'PCS', 1
    if kinds == {'PACK'}:
        return 'PACK', None
    return ('?' if kinds else ''), None


def strip_units(n):
    """'qty in pcs' / 'qty 3 pc pack' → 'qty': a quantity heading qualified by its unit is still a quantity heading."""
    words = n.split()
    if not any(_UNIT_TOKEN.fullmatch(t) for t in words):
        return n
    return ' '.join(t for t in words if not _UNIT_TOKEN.fullmatch(t) and t not in ('in', 'of', 'per') and not t.isdigit())


def unit_evidence(g, table, role=None):
    """Decide qty_unit from what the file itself says next to the quantities: the quantity heading (flat),
    the total / left headings (matrix), a unit label ("UOM | PCS") or a note just above the table.
    Returns {qty_unit, pieces_per_unit, provenance}; qty_unit is '' when the file is silent or says both."""
    sheet = g.sheet['name']; hr = table.get('header_row') if isinstance(table.get('header_row'), int) else 0
    heads = []
    if table.get('type') == 'flat':
        qc = (table.get('columns') or {}).get('quantity')
        if qc:
            heads.append((hr, int(qc), 'quantity heading'))
    else:
        heads += [(hr, int(table[k]), 'size table heading') for k in ('total_col', 'label_col', 'color_col') if table.get(k)]
    found = []
    for r, c, what in heads:
        v = g.v(r, c); u, per = unit_words(v)
        if u:
            found.append(dict(unit=u, per=per, cell=address(c, r), value=v, evidence=f'the {what} says “{text(v)}”'))
    for hit in g.find(g.fields.aliases('qty_unit'), range(1, max(2, hr)), first=False):
        t = g.right(*hit)
        if t:
            u, per = unit_words(g.v(*t))
            if u:
                found.append(dict(unit=u, per=per, cell=address(t[1], t[0]), value=g.v(*t),
                                  evidence=f'“{text(g.v(*hit))}” says “{text(g.v(*t))}”'))
    for r in range(max(1, hr - 2), hr):
        for c in range(1, g.cols + 1):
            v = g.v(r, c)
            if isinstance(v, str) and _QTY_WORDS.search(norm(v)):
                u, per = unit_words(v)
                if u:
                    found.append(dict(unit=u, per=per, cell=address(c, r), value=v, evidence=f'{address(c, r)} says “{text(v)}”'))
    units = {f['unit'] for f in found}
    said = text(g.v(*heads[0][:2])) if heads and table.get('type') == 'flat' else ''
    base = dict(sheet=sheet, cell=address(heads[0][1], heads[0][0]) if heads else None, value=said or None, said=said or None)
    if not found:
        evidence = (f'the quantity heading says only “{said}”' if said else
                    f'the {role or "size table"} does not say whether the quantities are pieces or packs')
        return dict(qty_unit='', pieces_per_unit=None, provenance=dict(base, evidence=evidence, reason='silent'))
    if '?' in units or units == {'PCS', 'PACK'}:
        return dict(qty_unit='', pieces_per_unit=None, provenance=dict(base, reason='both', evidence='the file mentions both pieces and packs (' +
                    '; '.join(f['evidence'] for f in found) + ')'))
    unit = units.pop(); pers = {f['per'] for f in found if f['per']}
    first = next(f for f in found if f['unit'] == unit)
    per = 1 if unit == 'PCS' else (pers.pop() if len(pers) == 1 else None)
    return dict(qty_unit=unit, pieces_per_unit=per, provenance=dict(sheet=sheet, cell=first['cell'], value=first['value'],
                                                                    evidence=first['evidence'], said=said or None, reason='file'))


def set_unit(o, unit):
    o['qty_unit'] = unit['qty_unit']
    if unit['qty_unit'] == 'PCS':
        o['pieces_per_unit'] = 1
    elif unit['qty_unit'] == 'PACK':
        o['pieces_per_unit'] = unit['pieces_per_unit']
    o.setdefault('provenance', {})['qty_unit'] = unit['provenance']
    if unit['qty_unit'] == 'PACK' and unit['pieces_per_unit']:
        o['provenance']['pieces_per_unit'] = dict(unit['provenance'], derivation=f'{unit["pieces_per_unit"]} pieces per pack: {unit["provenance"]["evidence"]}')
    return ask_unit(o)


def unit_question(o):
    """Plain question for an undecided unit, quoting what the file says (shown on the unit field)."""
    p = (o.get('provenance') or {}).get('qty_unit') or {}
    ev = p.get('evidence') or ''
    if p.get('reason') == 'both':
        return 'The file mentions both pieces and packs — which are these quantities?'
    if p.get('reason') in ('conflict', 'mismatch') and ev:
        return ev[0].upper() + ev[1:] + ' — are these quantities pieces or packs?'
    if p.get('said'):
        return f'The file says only “{p["said"]}” — are these pieces or packs?'
    return 'The file does not say whether these quantities are pieces or packs — which are they?'


def ask_unit(o):
    """Keep provenance.qty_unit.question in step with the unit (the planner and review screen show it)."""
    p = o.setdefault('provenance', {}).get('qty_unit')
    if o.get('qty_unit'):
        if isinstance(p, dict):
            p.pop('question', None)
    else:
        p = o['provenance']['qty_unit'] = dict(p or {})
        p['question'] = unit_question(o)
    return o


def matrix_blocks(g):
    """Size-matrix blocks: header row with garment + contract + sizes… + total."""
    blocks = []
    for r in range(1, min(g.rows, 400) + 1):
        gcol = next((c for c in range(1, g.cols + 1) if norm(g.v(r, c)) in ('garment', 'style', 'garment style')), None)
        if gcol is None:
            continue
        ccol = next((c for c in range(gcol + 1, g.cols + 1) if norm(g.v(r, c)) == 'contract'), None)
        tcol = next((c for c in range((ccol or gcol) + 1, g.cols + 1) if norm(g.v(r, c)) == 'total'), None)
        if ccol is None or tcol is None or tcol - ccol < 2:
            continue
        end = next((x for x in range(r + 1, min(r + 101, g.rows + 1)) if norm(g.v(x, gcol)) in ('total', 'grand total')), None)
        role = norm(g.v(r + 1, ccol))
        blocks.append(dict(header=r, end=end, gcol=gcol, ccol=ccol, tcol=tcol,
                           role=role if role in ('sample', 'summary') else 'order'))
    return blocks


def matrix_lines(g, header, end, label_col, color_col, first_size, total_col):
    result = []
    for r in range(header + 1, end):
        if g.v(r, label_col) is None and g.v(r, color_col) is None:
            continue
        result.append(dict(label=text(g.v(r, label_col)), color=text(g.v(r, color_col)),
            declared_total=g.v(r, total_col) if total_col else None, provenance={'color': g.p(r, color_col)},
            sizes=[dict(size=text(g.v(header, c)), quantity=g.v(r, c), source=g.p(r, c), size_source=g.p(header, c))
                   for c in range(first_size, (total_col or first_size) if total_col else first_size + 1)
                   if g.v(header, c) is not None]))
    return result


def merch_bom(g):
    blocks = matrix_blocks(g)
    if not blocks or not g.find(g.fields.aliases('buyer'), range(1, blocks[0]['header'])):
        return None
    o = base_order(); header_fields(g, o, blocks[0]['header'])
    roles = {}
    for b in blocks:
        if b['end'] is None:
            raise ValueError(f'{g.sheet["name"]}: incomplete garment/size block near row {b["header"]}.')
        if b['role'] in roles:
            raise ValueError(f'{g.sheet["name"]}: multiple {b["role"]} blocks need explicit mapping; they were not combined.')
        roles[b['role']] = b
    def lines(role):
        b = roles.get(role)
        return matrix_lines(g, b['header'], b['end'], b['gcol'], b['gcol'] + 1, b['ccol'] + 1, b['tcol']) if b else []
    o['order_lines'] = lines('order'); o['sample_lines'] = lines('sample')
    o['lines'] = deepcopy(o['sample_lines'] or o['order_lines'])
    o['quantity_basis'] = 'sample' if o['sample_lines'] else 'unconfirmed'
    o['warnings'] = ['Sample section selected. Order and summary repeats are excluded.' if o['sample_lines'] else
                     'No sample section found. Choose the quantity source.',
                     'BOM date is not an order confirmation date. Confirm the order date below.']
    packs(g, o, blocks[0]['header'])
    merch_unit(g, o)
    return o


def merch_unit(g, o):
    """A merch BOM's size quantities are pieces when the buyer-order size table adds up to its QTY(IN PIECES)."""
    total = sum(number(s.get('quantity')) or 0 for l in o['order_lines'] for s in l['sizes'])
    pk = o.get('packs') or {}; base = dict(sheet=g.sheet['name'])
    if pk.get('pieces') and total == pk['pieces']:
        cell = pk['source']['pieces']['cell']
        return set_unit(o, dict(qty_unit='PCS', pieces_per_unit=1, provenance=dict(
            base, cell=cell, value=pk['pieces'], reason='file', evidence=f'the size table total {total:g} equals QTY(IN PIECES) {pk["pieces"]:g} in {cell}')))
    if pk.get('pieces'):
        ev = f'the size table totals {total:g} but QTY(IN PIECES) says {pk["pieces"]:g}'
        set_unit(o, dict(qty_unit='', pieces_per_unit=None, provenance=dict(
            base, cell=pk['source']['pieces']['cell'], value=pk['pieces'], reason='mismatch', evidence=ev, said=None)))
    else:
        set_unit(o, dict(qty_unit='', pieces_per_unit=None, provenance=dict(
            base, reason='silent', evidence='the BOM does not say whether the size quantities are pieces or packs', said=None)))
    o['warnings'].append(unit_question(o))
    return o


def packs(g, o, limit_row):
    hp = g.find(['qty in packs', 'qty(in packs)', 'packs'], range(1, limit_row))
    hc = g.find(['qty in pieces', 'qty(in pieces)', 'pieces'], range(1, limit_row))
    if hp and hc:
        vp, vc = g.below(*hp), g.below(*hc)
        if vp and vc and number(g.v(*vp)) and number(g.v(*vc)):
            o['packs'] = dict(packs=number(g.v(*vp)), pieces=number(g.v(*vc)), source=dict(packs=g.p(*vp), pieces=g.p(*vc)))
            per = o['packs']['pieces'] / o['packs']['packs']
            if per.is_integer():
                o['components'] = int(per); o['packing_type'] = 'SINGLE' if per == 1 else f'{int(per)}PC PACK'
                note = f'{o["packs"]["pieces"]:g} pieces ÷ {o["packs"]["packs"]:g} packs'
                o['provenance']['components'] = dict(derivation=note, sheet=g.sheet['name'], cell=o['packs']['source']['pieces']['cell'])
                o['provenance']['packing_type'] = dict(derivation=f'Derived: {note}')


def buyer_contract(g, sheet):
    if not any(norm(c['value']) == 'contract confirmation report' for c in sheet['cells']):
        return None
    orders = []
    for cell in sheet['cells']:
        if norm(cell['value']) != 'item number':
            continue
        r, c = cell['row'], cell['col']; o = base_order()
        def setval(k, rr, cc):
            o[k] = text(g.v(rr, cc)); o['provenance'][k] = g.p(rr, cc)
        setval('style', r, c + 1); setval('description', r + 1, c + 1)
        header = next((x for x in range(r + 2, min(r + 15, g.rows + 1)) if norm(g.v(x, 6)) == 'contract no'), None)
        if not header:
            continue
        data = header + 1; setval('po_number', data, 6); setval('ship_date', data, 8)
        delivery = text(g.v(data, 7))
        if delivery:
            o['po_number'] += '-' + delivery
            o['provenance']['po_number']['derivation'] = 'Contract number + delivery suffix'
            o['provenance']['po_number']['delivery'] = g.p(data, 7)
        size_columns = [col for col in range(11, g.cols + 1) if g.v(header, col) is not None]
        if not size_columns:
            raise ValueError(f'{sheet["name"]}: no size headings found for {o["style"]}.')
        if any(norm(g.v(rr, 10)) == 'quantity' for rr in range(data + 1, min(data + 5, g.rows + 1))):
            raise ValueError(f'{sheet["name"]}: multiple deliveries for {o["style"]} require explicit separate orders.')
        line = dict(label=o['style'], color='', declared_total=g.v(data, 9), provenance={},
            sizes=[dict(size=text(g.v(header, col)), quantity=g.v(data, col), source=g.p(data, col), size_source=g.p(header, col)) for col in size_columns])
        o['order_lines'] = [line]; o['lines'] = deepcopy(o['order_lines'])
        o['warnings'] = ['Buyer contract quantities are not identified as sample quantities. Confirm the quantity basis and pieces per source unit.',
            'Buyer, colour and order confirmation date require review; destination is not assumed to be buyer.']
        set_unit(o, unit_evidence(g, dict(type='matrix', header_row=header, total_col=9, label_col=10), role='contract'))
        orders.append(o)
    return orders


def line_number(value):
    m = re.search(r'garment\s*-?\s*(\d+)', str(value or ''), re.I)
    return int(m.group(1)) if m else None


def fabrics(g):
    """FABRIC REQUIREMENT style tables → fabric rows with suggested yarn split."""
    out = []
    for r in range(1, min(g.rows, 1000) + 1):
        cols = g.header_columns(r, FABRIC_COLUMNS)
        if not ({'component', 'quality', 'yy'} <= cols.keys() and ('gsm' in cols or 'dia' in cols)):
            continue
        for rr in range(r + 1, min(r + 200, g.rows + 1)):
            comp = text(g.v(rr, cols['component']))
            if not comp or norm(comp) in ('total', 'grand total'):
                break
            def val(k):
                return g.v(rr, cols[k]) if k in cols else None
            row = dict(component=comp, line=line_number(comp),
                       portion=next((p for p in PORTIONS if p in comp.upper()), ''),
                       color=text(val('color')), base_color=text(val('base_color')), quality=text(val('quality')),
                       gsm=text(val('gsm')), content=text(val('content')), count=text(val('count')), dia=text(val('dia')),
                       order_qty=number(val('order_qty')), yy=number(val('yy')), requirement=number(val('requirement')),
                       rejection=number(val('rejection')), total=number(val('total')),
                       source={k: g.p(rr, c) for k, c in cols.items()})
            row['yarns'] = suggest_yarns(row)
            out.append(row)
    return out


def suggest_yarns(f):
    content = f.get('content') or ''
    parts = re.findall(r'(\d+(?:\.\d+)?)\s*%\s*([A-Za-z][A-Za-z /]*)', content)
    if not parts:
        return []
    count = f.get('count') or ''
    note = f'Suggested from fabric content "{content}"' + (f' and count "{count}"' if count else '') + ' — confirm before saving.'
    yarns = []
    for i, (pct, fibre) in enumerate(parts):
        fibre = fibre.split('-')[0].strip().upper()
        yarns.append(dict(count=f'{count} {fibre}'.strip() if i == 0 else fibre, color='GREIGE', percent=float(pct),
                          description=fibre, source={'derivation': note}))
    return yarns


def accessories(g):
    out = []; previous_title = ''
    for r in range(1, min(g.rows, 2000) + 1):
        cols = g.header_columns(r, ACC_COLUMNS)
        if not ({'first', 'order_qty', 'total'} <= cols.keys() and ('required' in cols or 'yy' in cols)):
            continue
        title = ''
        for rr in (r - 1, r - 2):
            row_cells = [g.v(rr, cc) for cc in range(1, g.cols + 1) if g.v(rr, cc) not in (None, '')]
            # A title row is a short text-only row; subtotal rows (numbers or "TOTAL") are not titles.
            if row_cells and all(isinstance(x, str) and number(x) is None for x in row_cells) \
                    and not any(norm(x) in ('total', 'grand total') for x in row_cells):
                title = text(row_cells[0]); break
        title = title or previous_title; previous_title = title
        unit_header = norm(g.v(r, cols['total']))
        uom = 'MTR' if 'mtr' in unit_header else 'KGS' if 'kg' in unit_header else 'PCS'
        carry = {}
        for rr in range(r + 1, min(r + 200, g.rows + 1)):
            texts = {k: text(g.v(rr, cols[k])) for k in ('first', 'code', 'size', 'color') if k in cols}
            values = [g.v(rr, c) for c in cols.values()]
            if all(v in (None, '') for v in values):
                break
            row_text = [norm(g.v(rr, cc)) for cc in range(1, g.cols + 1) if isinstance(g.v(rr, cc), str)]
            if any(t in ('total', 'grand total') for t in row_text) or not any(texts.values()):
                break  # subtotal row (labelled TOTAL or unlabelled numbers)
            for k in ('first', 'code', 'color'):
                # Visually merged block cells: carry within this block only.
                if texts.get(k):
                    carry[k] = texts[k]
                elif k in carry:
                    texts[k] = carry[k]
            supplier = text(g.v(rr, cols['supplier'])) if 'supplier' in cols else ''
            if supplier:
                carry['supplier'] = supplier
            first = texts.get('first', '')
            garment_ref = bool(re.match(r'(garment|all garments|all combos|all colou?rs?)', first, re.I))
            name = (title or first) if garment_ref else first or title
            code = texts.get('code', '')
            quality = text(g.v(rr, cols['quality'])) if 'quality' in cols else ''
            if quality:
                carry['quality'] = quality
            quality = quality or carry.get('quality', '')
            out.append(dict(category=title, block=r, name=name, code=code, color=texts.get('color', ''),
                            size=texts.get('size', ''), supplier=supplier or carry.get('supplier', ''),
                            line=line_number(first), uom=uom,
                            description=' '.join(x for x in (name if name != code else '', code, quality) if x).strip()[:120],
                            order_qty=number(g.v(rr, cols['order_qty'])), yy=number(g.v(rr, cols['yy'])) if 'yy' in cols else None,
                            required=number(g.v(rr, cols['required'])) if 'required' in cols else None,
                            rejection=number(g.v(rr, cols['rejection'])) if 'rejection' in cols else None,
                            total=number(g.v(rr, cols['total'])), source={k: g.p(rr, c) for k, c in cols.items()}))
    return out


def attach_bom(g, orders):
    fab, acc = fabrics(g), accessories(g)
    if not (fab or acc) or not orders:
        return
    if len(orders) > 1:
        orders[0]['warnings'].append('Fabric/accessory tables found on a sheet with several orders; they were not attached automatically.')
        return
    o = orders[0]
    o['fabrics'], o['accessories'] = fab, acc
    if fab:
        o['warnings'].append(f'{len(fab)} fabric rows found. Yarn splits are suggested from fabric content — confirm them.')
    if acc:
        o['warnings'].append(f'{len(acc)} accessory rows found in {len({a["block"] for a in acc})} trim tables.')
        seen = {}
        for a in acc:
            for other in seen.get((norm(a['category']), a['line']), set()) - {a['block']}:
                o['warnings'].append(f'"{a["category"]}" for garment {a["line"] or "(all)"} appears in two tables '
                                     f'(rows {other} and {a["block"]}). Check it is not counted twice; remove the duplicate rows if so.')
            seen.setdefault((norm(a['category']), a['line']), set()).add(a['block'])
        o['warnings'] = list(dict.fromkeys(o['warnings']))


DATE_FIELDS = ('ship_date', 'order_date')
MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


def iso_date(value):
    """(YYYY-MM-DD or None, note). Buyer files write dates many ways; numbers are read day-first
    (Indian convention) and an ambiguous day/month is reported, never silently guessed."""
    if value is None or value == '':
        return None, None
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10], None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 <= value <= 80000:      # Excel serial day number (1954–2119)
            from datetime import timedelta
            return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat(), f'Excel date number {value:g} read as a date'
        return None, None
    t = str(value).strip()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?', t)
    if m:
        y, mo, d = map(int, m.groups()); note = None
    else:
        m = re.fullmatch(r'(\d{4})[/.](\d{1,2})[/.](\d{1,2})', t)
        if m:
            y, mo, d = map(int, m.groups()); note = None
        else:
            m = re.fullmatch(r'(\d{1,2})[-/. ]([A-Za-z]{3,9})[-/., ]+(\d{2,4})', t)
            if m and m.group(2)[:3].lower() in MONTHS:
                d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3)); note = None
            else:
                m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})', t)
                if not m:
                    return None, None
                d, mo, y = map(int, m.groups())
                note = f'"{t}" read as day/month/year' if d <= 12 and mo <= 12 and d != mo else None
                if mo > 12 and d <= 12:      # unmistakably month/day/year
                    d, mo = mo, d; note = f'"{t}" read as month/day/year'
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d).isoformat(), note
    except ValueError:
        return None, None


def finish(o):
    o['workbook_pieces'] = sum(int(number(s.get('quantity')) or 0) for l in o.get('order_lines', []) for s in l.get('sizes', [])) or None
    for key in DATE_FIELDS:
        raw = o.get(key)
        if raw in (None, ''):
            continue
        iso, note = iso_date(raw)
        if iso and iso != raw:
            o[key] = iso
            o.setdefault('provenance', {}).setdefault(key, {})['derivation'] = note or f'"{raw}" converted to {iso}'
            if note and 'day/month' in note:
                o.setdefault('warnings', []).append(f'{key.replace("_", " ").capitalize()}: {note} — check it.')
        elif not iso:
            o.setdefault('warnings', []).append(f'{key.replace("_", " ").capitalize()} "{raw}" is not a date this product can read — enter it.')
            o[key] = ''
    return o


def parse(content, templates=(), filename='', fields=None):
    fmt, sheets = readers.read(content, filename)
    out = parse_sheets(sheets, templates, fields)
    out['source_format'] = readers.LABELS[fmt]
    return out


def flat_headers(g):
    """(header row, {field: column}) for each row that heads a one-row-per-size table: size and quantity
    headings (a quantity heading may carry its unit, "Qty (pcs)"); style is a column or a header field."""
    Fv = g.fields; names = {k: {norm(a) for a in Fv.aliases(k)} for k in Fv.column_keys}
    quantity = names.get('quantity', set())
    for row in range(1, min(g.rows, 80) + 1):
        cells = [(c, norm(g.v(row, c))) for c in range(1, g.cols + 1) if isinstance(g.v(row, c), str)]
        hit = {key: c for c, n in cells for key in Fv.column_keys if n in names[key]}
        if 'quantity' not in hit:
            q = next((c for c, n in cells if strip_units(n) != n and strip_units(n) in quantity), None)
            if q:
                hit['quantity'] = q
        if {'size', 'quantity'} <= hit.keys():
            yield row, hit


AUTO_NOTES = ('Confirm whether these are sample quantities', 'Confirm whether the source quantities', 'The file says',
              'The file mentions both', 'The file does not say whether', 'The size table totals', 'Read with the saved layout')


def review_notes(o, lead=None):
    """Replace the automatic unit/basis notes of an order with ones that are true now."""
    ask_unit(o)
    o['warnings'] = [w for w in o.get('warnings', []) if not w.startswith(AUTO_NOTES)]
    if lead:
        o['warnings'].insert(0, lead)
    if not o.get('qty_unit'):
        o['warnings'].append(unit_question(o))
    if o.get('quantity_basis') not in ('sample', 'order_demo'):
        o['warnings'].append('Confirm whether these are sample quantities.')
    return o


def anchor_problems(g, t):
    """Anchors whose label cell no longer says what it said when the template was saved."""
    out = []
    for key, a in sorted((t.get('anchors') or {}).items()):
        if not a or not a.get('label_cell') or not a.get('label'):
            continue
        try:
            now = g.v(*cell_ref(a['label_cell']))
        except ValueError:
            continue
        if norm(now) != norm(a['label']):
            out.append(f'{a["label_cell"]} now says “{text(now)}”, not “{a["label"]}”' if now not in (None, '') else
                       f'{a["label_cell"]} is empty; it said “{a["label"]}”')
    return out


def choose_template(sheets, sheet, templates, fields):
    """(template, orders, refusals). A template is applied only when the layout matches, every anchor label
    is unchanged and the validator accepts its definition on this sheet; otherwise the reason is kept."""
    from . import matcher
    g = Grid(sheet, fields); refused = []
    ranked = sorted(((template_score(sheet, t), t) for t in templates), key=lambda x: -x[0])
    for score, t in ranked:
        if score < 0.8:
            break
        reasons = anchor_problems(g, t)
        if not reasons:
            _, rejected = matcher.check(sheet, t['definition'], fields)
            reasons = [r['reason'] for r in rejected]
        orders = None
        if not reasons:
            try:
                orders = apply_template(sheets, sheet, t['definition'], fields)
            except ValueError as exc:
                reasons = [str(exc)]
        if not reasons:
            return t, orders, refused
        refused.append(dict(id=t['id'], name=t['name'], version=t.get('version', 1), sheet=sheet['name'], reasons=reasons))
    return None, None, refused


def restore_from_template(orders, t):
    """A confirmed unit and quantity basis saved with the template come back; the file still wins when it
    says something different (then the person is asked)."""
    tag = f'saved layout “{t["name"]}” (version {t.get("version", 1)})'
    unit = t.get('unit') or {}; basis = t.get('quantity_basis')
    for o in orders:
        prov = o.setdefault('provenance', {})
        if unit.get('qty_unit') in ('PCS', 'PACK'):
            per = 1 if unit['qty_unit'] == 'PCS' else unit.get('pieces_per_unit')
            file_unit = (prov.get('qty_unit') or {}).get('reason') == 'file'
            if not o.get('qty_unit') and not file_unit:
                o['qty_unit'] = unit['qty_unit']; o['pieces_per_unit'] = per
                prov['qty_unit'] = dict(derivation=f'Confirmed for the {tag}', reason='template', template=t['id'])
                prov['pieces_per_unit'] = dict(derivation=f'Confirmed for the {tag}', reason='template', template=t['id'])
            elif o.get('qty_unit') and (o['qty_unit'] != unit['qty_unit'] or
                                        (o['qty_unit'] == 'PACK' and o.get('pieces_per_unit') and per and o['pieces_per_unit'] != per)):
                said = 'pieces' if o['qty_unit'] == 'PCS' else f'packs{" of " + str(o["pieces_per_unit"]) if o.get("pieces_per_unit") else ""}'
                saved = 'pieces' if unit['qty_unit'] == 'PCS' else f'packs{" of " + str(per) if per else ""}'
                prov['qty_unit'] = dict(prov.get('qty_unit') or {}, reason='conflict',
                                        evidence=f'the file says {said} but the {tag} says {saved}')
                o['qty_unit'] = ''; o['pieces_per_unit'] = None
            elif o.get('qty_unit') == 'PACK' and not o.get('pieces_per_unit') and per:
                o['pieces_per_unit'] = per
                prov['pieces_per_unit'] = dict(derivation=f'Confirmed for the {tag}', reason='template', template=t['id'])
        if basis in ('sample', 'order_demo') and o.get('quantity_basis') not in ('sample', 'order_demo'):
            o['quantity_basis'] = basis
            prov['quantity_basis'] = dict(derivation=f'Confirmed for the {tag}', reason='template', template=t['id'])
        review_notes(o, f'Read with the {tag}.')
    return orders


def parse_sheets(sheets, templates=(), fields=None):
    from . import matcher
    Fv = F.resolve(fields)
    orders = []; formats = []; used = []; notes = []; refusals = []
    for sheet in sheets:
        if sheet['hidden']:
            continue
        g = Grid(sheet, Fv); found = None; mine = []
        if templates:
            t, found, refused = choose_template(sheets, sheet, templates, Fv)
            for r in refused:
                mine.append(f'Saved layout “{r["name"]}” (version {r["version"]}) was not used on sheet “{r["sheet"]}”: '
                            + '; '.join(r['reasons']) + '. The file was read as a new layout.')
            refusals += refused
            if t:
                restore_from_template(found, t)
                formats.append(f'Template: {t["name"]}'); used.append(t['id'])
        if not found:
            o = merch_bom(g)
            if o:
                found = [o]; formats.append('Merch BOM')
        if not found:
            found = buyer_contract(g, sheet)
            if found is not None:
                formats.append('Buyer contract')
        if not found:
            for row, cols in flat_headers(g):
                d = dict(fields={}, table=dict(type='flat', header_row=row, columns=cols))
                if 'style' not in cols:
                    at = header_value(g, 'style', row)
                    if not at:
                        continue
                    d['fields']['style'] = address(at[1], at[0])
                _, rejected = matcher.check(sheet, d, Fv)
                if rejected:
                    mine.append(f'The size/quantity table on “{sheet["name"]}” row {row} was not read automatically: '
                                + '; '.join(r['reason'] for r in rejected) + '.')
                    continue
                found = apply_template(sheets, sheet, d, Fv); formats.append('Tabular orders')
                for o in found:
                    header_fields(g, o, row, only_missing=True)
                break
        notes += mine
        if found:
            for o in found:
                o['warnings'] += [n for n in mine if n not in o['warnings']]
            attach_bom(g, found)
            uncalculated = [c['cell'] for c in sheet['cells'] if c.get('formula') and c.get('value') is None]
            if uncalculated:
                # Files saved by tools other than Excel can lack calculated formula results.
                for o in found:
                    o['warnings'].insert(0, f'{len(uncalculated)} formula cells on "{sheet["name"]}" have no calculated value '
                                            f'(e.g. {", ".join(uncalculated[:4])}). Open the file in Excel, save it, and upload again.')
            orders.extend(finish(o) for o in found)
    return dict(sheets=sheets, orders=orders, format=', '.join(dict.fromkeys(formats)) or 'Needs column mapping',
                templates_used=used, notes=notes, templates_refused=refusals)


def parse_mapping(sheets, mapping, fields=None):
    Fv = F.resolve(fields)
    sheet = next((s for s in sheets if s['name'] == mapping['sheet']), None)
    if not sheet:
        raise ValueError('Select a worksheet.')
    row = int(mapping['header_row']); columns = mapping['columns']
    if not {'style', 'size', 'quantity'} <= columns.keys():
        raise ValueError('Map style, size and quantity columns at minimum.')
    if any(k not in Fv.column_keys or not isinstance(c, int) or c < 1 or c > sheet['cols'] for k, c in columns.items()):
        raise ValueError('Invalid column mapping.')
    cells = {(c['row'], c['col']): c for c in sheet['cells']}; groups = {}
    for r in range(row + 1, sheet['rows'] + 1):
        fields = {k: cells.get((r, c), {}).get('value') for k, c in columns.items()}
        if not any(x is not None for x in fields.values()):
            continue
        if norm(fields.get('style')) in ('total', 'grand total'):
            continue
        # Blank styles cannot safely be forward-filled across unrelated sections.
        if not fields.get('style'):
            raise ValueError(f"{sheet['name']} row {r}: style is missing. Fill it or choose the correct header.")
        key = tuple(text(fields.get(k)) for k in ('buyer', 'style', 'po_number', 'order_ref', 'ship_date'))
        o = groups.setdefault(key, base_order())
        for k, value in fields.items():
            if k not in ('size', 'quantity', 'color'):
                if o.get(k) and o[k] != text(value):
                    raise ValueError(f'Conflicting {k} in row {r}.')
                o[k] = text(value)
                c = cells.get((r, columns[k]), {})
                o['provenance'][k] = source(sheet['name'], c.get('cell', ''), value, c.get('formula'))
        color = text(fields.get('color'))
        line = next((x for x in o['lines'] if x['color'] == color), None)
        if line is None:
            line = dict(label=o['style'], color=color, declared_total=None, sizes=[], provenance={}); o['lines'].append(line)
        qc = cells.get((r, columns['quantity']), {})
        line['sizes'].append(dict(size=text(fields.get('size')), quantity=fields.get('quantity'),
                                 source=source(sheet['name'], qc.get('cell', ''), fields.get('quantity'), qc.get('formula'))))
    for o in groups.values():
        o['warnings'] = ['Confirm whether the source quantities represent pieces or packs, and whether they are sample quantities.']
        o['order_lines'] = deepcopy(o['lines'])
    return list(groups.values())


def validate_definition(sheet, d, fields=None):
    """A template/mapping definition refers only to known fields and real cells of this sheet."""
    Fv = F.resolve(fields)
    fields = d.get('fields') or {}
    for k, ref in fields.items():
        if k not in Fv.header_keys:
            raise ValueError(f'Unknown header field {k}.')
        cell_ref(ref)
    t = d.get('table') or {}
    kind = t.get('type')
    if kind == 'flat':
        if not isinstance(t.get('header_row'), int) or t['header_row'] < 1:
            raise ValueError('Choose the header row of the table.')
        if not {'style', 'size', 'quantity'} <= set(t.get('columns', {})) and not ('style' in fields and {'size', 'quantity'} <= set(t.get('columns', {}))):
            raise ValueError('Map size and quantity columns, and style either as a column or a header cell.')
    elif kind == 'matrix':
        for k in ('header_row', 'first_size_col', 'last_size_col'):
            if not isinstance(t.get(k), int) or t[k] < 1:
                raise ValueError(f'Size matrix needs {k.replace("_", " ")}.')
        if t['last_size_col'] < t['first_size_col']:
            raise ValueError('Last size column must be after the first size column.')
    else:
        raise ValueError('Choose a table type: one row per size, or sizes across columns.')
    return d


def apply_template(sheets, sheet, d, fields=None):
    Fv = F.resolve(fields)
    validate_definition(sheet, d, Fv)
    g = Grid(sheet, Fv); fields = d.get('fields') or {}; t = d['table']
    if t['type'] == 'flat':
        cols = {k: int(v) for k, v in t['columns'].items()}
        if 'style' not in cols:
            # Header style applies to all rows: synthesise a constant column via the grouping key below.
            r, c = cell_ref(fields['style']); style = text(g.v(r, c))
            temp = deepcopy(sheet); temp['cols'] += 1
            temp['cells'] = temp['cells'] + [dict(row=rr, col=temp['cols'], cell=f'{fields["style"]}', value=style, formula=None)
                                             for rr in range(int(t['header_row']) + 1, sheet['rows'] + 1)
                                             if any(g.v(rr, cc) not in (None, '') for cc in (cols['size'], cols['quantity']))]
            cols['style'] = temp['cols']
            orders = parse_mapping([temp], dict(sheet=sheet['name'], header_row=int(t['header_row']), columns=cols), Fv)
        else:
            orders = parse_mapping(sheets, dict(sheet=sheet['name'], header_row=int(t['header_row']), columns=cols), Fv)
    else:
        header = int(t['header_row']); last = int(t['last_size_col'])
        label_col = int(t.get('label_col') or 0) or None; color_col = int(t.get('color_col') or 0) or None
        total_col = int(t.get('total_col') or 0) or None
        anchor = label_col or color_col or int(t['first_size_col']) - 1
        end = next((r for r in range(header + 1, min(header + 201, g.rows + 2))
                    if norm(g.v(r, anchor)) in ('total', 'grand total') or all(g.v(r, c) in (None, '') for c in range(int(t['first_size_col']), last + 1))), g.rows + 1)
        o = base_order()
        lines = []
        for r in range(header + 1, end):
            lines.append(dict(label=text(g.v(r, label_col)) if label_col else '', color=text(g.v(r, color_col)) if color_col else '',
                              declared_total=g.v(r, total_col) if total_col else None,
                              provenance={'color': g.p(r, color_col)} if color_col else {},
                              sizes=[dict(size=text(g.v(header, c)), quantity=g.v(r, c), source=g.p(r, c), size_source=g.p(header, c))
                                     for c in range(int(t['first_size_col']), last + 1) if g.v(header, c) is not None]))
        o['lines'] = lines; o['order_lines'] = deepcopy(lines)
        orders = [o]
    unit = unit_evidence(g, t)
    for o in orders:
        for k, ref in fields.items():
            r, c = cell_ref(ref)
            o[k] = field_value(g, r, c, k); o['provenance'][k] = g.p(r, c)
        review_notes(set_unit(o, deepcopy(unit)))
    return orders
