"""Read-only document ingestion. Workbook/PDF text is data, never executable instructions.

Pipeline per sheet: saved user template (exact layout signature) → built-in structural
detectors (merch BOM, buyer contract) → alias-matched flat table → "needs mapping".
BOM tables (fabric, accessories) are detected by their header structure on the same sheet.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime
from io import BytesIO
import hashlib
import re
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook

ALIASES = {
 'buyer': ['buyer', 'buyer name', 'customer', 'customer name', 'brand'],
 'style': ['style', 'style no', 'style number', 'item', 'item number', 'style #', 'item no', 'article', 'article no'],
 'description': ['description', 'garment description', 'item description', 'style description'],
 'po_number': ['po', 'po number', 'po no', 'contract', 'contract no', 'order no', 'purchase order no', 'po #'],
 'order_ref': ['oc no', 'order reference', 'order ref', 'docid', 'ocn'],
 'ship_date': ['ship date', 'ship dt', 'delivery date', 'ex factory date', 'ex-factory date', 'fob date', 'etd'],
 'season': ['season'],
 'merch': ['merchandiser', 'merch', 'merchant'],
 'size': ['size', 'sizes'], 'quantity': ['quantity', 'qty', 'sample qty', 'order qty', 'pcs', 'pieces'],
 'color': ['colour', 'color', 'combo', 'colour description', 'color description', 'colour name'],
}
HEADER_FIELDS = ('buyer', 'style', 'description', 'po_number', 'order_ref', 'ship_date', 'season', 'merch')
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
        pieces_per_unit=None, quantity_basis='unconfirmed', lines=[], provenance={},
        warnings=[], order_lines=[], sample_lines=[], fabrics=[], accessories=[], packs=None, workbook_pieces=None)


def inspect(content):
    if len(content) > 12 * 1024 * 1024:
        raise ValueError('Workbook exceeds 12 MB.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 10000 or sum(z.file_size for z in archive.infolist()) > 80 * 1024 * 1024:
                raise ValueError('Workbook expanded size exceeds 80 MB.')
            if '[Content_Types].xml' not in archive.namelist():
                raise ValueError('Please upload an Excel .xlsx or .xlsm workbook.')
    except BadZipFile as exc:
        raise ValueError('Please upload an unencrypted .xlsx or .xlsm workbook.') from exc
    raw = load_workbook(BytesIO(content), data_only=False, keep_links=False)
    cached = load_workbook(BytesIO(content), data_only=True, keep_links=False)
    sheets = []
    if len(raw.worksheets) > 30:
        raise ValueError('Workbook exceeds 30 sheets.')
    for ws in raw:
        if ws.max_row * ws.max_column > 250000:
            raise ValueError(f'{ws.title}: sheet exceeds 250,000 cells; trim unused formatting.')
        cells = []
        for row in ws:
            for cell in row:
                if cell.value is not None:
                    formula = cell.value if cell.data_type == 'f' else None
                    value = scalar(cached[ws.title][cell.coordinate]) if formula else scalar(cell)
                    cells.append(dict(cell=cell.coordinate, row=cell.row, col=cell.column, value=value,
                        formula=formula, hidden=bool(ws.row_dimensions[cell.row].hidden or ws.column_dimensions[cell.column_letter].hidden),
                        error=cell.data_type == 'e'))
        sheets.append(dict(name=ws.title, hidden=ws.sheet_state != 'visible', rows=ws.max_row, cols=ws.max_column,
                           merged=[str(r) for r in ws.merged_cells.ranges], cells=cells))
    raw.close(); cached.close()
    return sheets


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
    return hashlib.sha256('|'.join(sorted(labels(sheet))).encode()).hexdigest()


def similarity(a, b):
    """Position-aware Jaccard similarity between two label sets."""
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def match_template(sheet, templates, threshold=0.8):
    mine = labels(sheet); best = None
    for t in templates:
        score = 1.0 if t['signature'] == signature(sheet) else similarity(mine, t['labels'])
        if score >= threshold and (best is None or score > best[0]):
            best = (score, t)
    return best


# --- cell helpers ----------------------------------------------------------------
class Grid:
    def __init__(self, sheet):
        self.sheet = sheet
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
LABEL_WORDS = {norm(a) for aliases in ALIASES.values() for a in aliases} | {'bom date', 'fab ih date', 'destination'}


def looks_like_label(value):
    """A neighbour that is itself a heading (e.g. "QTY(IN PACKS)") is not the value of a label."""
    n = norm(value)
    return isinstance(value, str) and (n in LABEL_WORDS or bool(re.search(r'\b(qty|quantity|price|total)\b', n)))


def header_fields(g, o, limit_row, only_missing=False):
    """Key-value labels ("BUYER | NEXT") anywhere above the first data block."""
    rows = range(1, max(2, limit_row))
    for key in HEADER_FIELDS:
        if only_missing and o.get(key):
            continue
        target = next((t for hit in g.find(ALIASES[key], rows, first=False) for t in [g.right(*hit)]
                       if t and not looks_like_label(g.v(*t))), None)
        if target:
            o[key] = text(g.v(*target)); o['provenance'][key] = g.p(*target)


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
    if not blocks or not g.find(ALIASES['buyer'], range(1, blocks[0]['header'])):
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
    o['quantity_basis'] = 'sample' if o['sample_lines'] else 'unconfirmed'; o['pieces_per_unit'] = 1
    o['warnings'] = ['Sample section selected. Order and summary repeats are excluded.' if o['sample_lines'] else
                     'No sample section found. Choose the quantity source.',
                     'BOM date is not an order confirmation date. Confirm the order date below.']
    packs(g, o, blocks[0]['header'])
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


def finish(o):
    o['workbook_pieces'] = sum(int(number(s.get('quantity')) or 0) for l in o.get('order_lines', []) for s in l.get('sizes', [])) or None
    return o


def parse(content, templates=()):
    return parse_sheets(inspect(content), templates)


def parse_sheets(sheets, templates=()):
    orders = []; formats = []; used = []
    for sheet in sheets:
        if sheet['hidden']:
            continue
        g = Grid(sheet); found = None
        match = match_template(sheet, templates) if templates else None
        if match:
            found = apply_template(sheets, sheet, match[1]['definition'])
            formats.append(f'Template: {match[1]["name"]}'); used.append(match[1]['id'])
        if not found:
            o = merch_bom(g)
            if o:
                found = [o]; formats.append('Merch BOM')
        if not found:
            found = buyer_contract(g, sheet)
            if found is not None:
                formats.append('Buyer contract')
        if not found:
            mapping = None
            for row in range(1, min(sheet['rows'], 80) + 1):
                hit = {key: cell['col'] for cell in sheet['cells'] if cell['row'] == row
                       for key, aliases in ALIASES.items() if norm(cell['value']) in [norm(a) for a in aliases]}
                if {'style', 'size', 'quantity'} <= hit.keys():
                    mapping = {'sheet': sheet['name'], 'header_row': row, 'columns': hit}; break
            if mapping:
                found = parse_mapping(sheets, mapping); formats.append('Tabular orders')
                for o in found:
                    header_fields(g, o, mapping['header_row'], only_missing=True)
        if found:
            attach_bom(g, found)
            uncalculated = [c['cell'] for c in sheet['cells'] if c.get('formula') and c.get('value') is None]
            if uncalculated:
                # Files saved by tools other than Excel can lack calculated formula results.
                for o in found:
                    o['warnings'].insert(0, f'{len(uncalculated)} formula cells on "{sheet["name"]}" have no calculated value '
                                            f'(e.g. {", ".join(uncalculated[:4])}). Open the file in Excel, save it, and upload again.')
            orders.extend(finish(o) for o in found)
    return dict(sheets=sheets, orders=orders, format=', '.join(dict.fromkeys(formats)) or 'Needs column mapping',
                templates_used=used)


def parse_mapping(sheets, mapping):
    sheet = next((s for s in sheets if s['name'] == mapping['sheet']), None)
    if not sheet:
        raise ValueError('Select a worksheet.')
    row = int(mapping['header_row']); columns = mapping['columns']
    if not {'style', 'size', 'quantity'} <= columns.keys():
        raise ValueError('Map style, size and quantity columns at minimum.')
    if any(k not in ALIASES or not isinstance(c, int) or c < 1 or c > sheet['cols'] for k, c in columns.items()):
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


def validate_definition(sheet, d):
    """A template/mapping definition refers only to known fields and real cells of this sheet."""
    fields = d.get('fields') or {}
    for k, ref in fields.items():
        if k not in HEADER_FIELDS:
            raise ValueError(f'Unknown header field {k}.')
        cell_ref(ref)
    t = d.get('table') or {}
    kind = t.get('type')
    if kind == 'flat':
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


def apply_template(sheets, sheet, d):
    validate_definition(sheet, d)
    g = Grid(sheet); fields = d.get('fields') or {}; t = d['table']
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
            orders = parse_mapping([temp], dict(sheet=sheet['name'], header_row=int(t['header_row']), columns=cols))
        else:
            orders = parse_mapping(sheets, dict(sheet=sheet['name'], header_row=int(t['header_row']), columns=cols))
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
        o['warnings'] = ['Read with a saved layout template. Confirm the quantity source and pieces per unit.']
        orders = [o]
    for o in orders:
        for k, ref in fields.items():
            r, c = cell_ref(ref)
            o[k] = text(g.v(r, c)); o['provenance'][k] = g.p(r, c)
    return orders
