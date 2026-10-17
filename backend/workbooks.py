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
from datetime import date, datetime, timedelta
from io import BytesIO
import hashlib
import math
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


norm = F.norm      # one normalisation for labels everywhere (keeps the letters of every script)


def scalar(cell):
    value = cell.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, (int, float)) and re.fullmatch(r'0{2,}', cell.number_format):
        return str(int(value)).zfill(len(cell.number_format))
    return value.strip() if isinstance(value, str) else value


def text(value):
    return '' if value is None else str(value).strip()


# Plain numbers, and Western (1,500,000) or Indian lakh (1,20,000) digit grouping. A comma anywhere else is not
# a thousands separator: "1,5" is a decimal comma (or a typo) and is never read as 15.
_PLAIN = re.compile(r'[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?')
_GROUPED = re.compile(r'[-+]?(\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})+,\d{3})(\.\d+)?')


def number(value):
    """The number a cell holds, or None. Text that no number can hold ("1E+400") is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    t = str(value).strip()
    if _PLAIN.fullmatch(t):
        n = float(t)
    elif _GROUPED.fullmatch(t):
        n = float(t.replace(',', ''))
    else:
        return None
    return n if math.isfinite(n) else None


_QTY_TEXT = re.compile(r'([-+]?[\d,]*\.?\d+)\s*(pcs|pc|pieces|piece|nos|packs|pack|pks|pk|sets|set)?\.?', re.I)


def qty_value(value):
    """(number, 'PCS' | 'PACK' | None) of a quantity cell: 1500 → (1500, None), '1,500' → (1500, None),
    '1,500 pcs' → (1500, 'PCS'), '20 packs' → (20, 'PACK'); anything else → (None, None)."""
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)):
        return (value, None) if number(value) is not None else (None, None)
    m = _QTY_TEXT.fullmatch(str(value).strip())
    n = number(m.group(1)) if m else None
    if n is None:
        return None, None
    n = int(n) if float(n).is_integer() else n
    word = (m.group(2) or '').lower()
    return n, ('PACK' if word.startswith(('pack', 'pk', 'set')) else 'PCS' if word else None)


def quantity_of(value):
    """A quantity as the order keeps it: numbers written as text ("1,500", "1,500 pcs") become the number;
    anything else (and "2.000", which could be 2000 or 2) is kept as it is, so the planner names it."""
    n, _ = qty_value(value)
    return n if n is not None and isinstance(value, str) and not dotted(value) else value


_DOTTED = re.compile(r'[-+]?[1-9]\d{0,2}(\.\d{3})+')


def dotted(value):
    """Text such as “2.000” or “12.500 pcs”: a dot between thousands (2000, as decimal-comma countries write it) or a
    decimal point (2)? Quantities are whole numbers, so in any file format it is never read as either."""
    m = _QTY_TEXT.fullmatch(value.strip()) if isinstance(value, str) else None
    return bool(m) and bool(_DOTTED.fullmatch(m.group(1)))


# Excel error values: a formula that failed in the buyer's file (a broken link, a VLOOKUP that found nothing).
_ERROR = re.compile(r'#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA|SPILL!|CALC!|FIELD!|BLOCKED!|CONNECT!|BUSY!|UNKNOWN!)', re.I)


def is_error(value):
    return isinstance(value, str) and bool(_ERROR.fullmatch(value.strip()))


# A row that adds up other rows: "Total", "TOTAL PCS", "Sub-Total", "G. TTL", "Colour Total", "ZK-100 Total", "SUM",
# "Grand Total:". Words after the total word may only be quantity/unit words ("Total Qty") or start with of/for
# ("Total for RED"); otherwise ("TOTAL ECLIPSE") the label may be a colour and the row's numbers decide (matrix_orders).
_TOTAL_WORDS = frozenset({'total', 'totals', 'ttl', 'sum', 'subtotal', 'subtotals', 'subttl', 'grandtotal', 'gtotal', 'gttl'})
_TOTAL_AFTER = frozenset({'qty', 'quantity', 'quantities', 'pcs', 'pc', 'pieces', 'piece', 'packs', 'pack', 'pks', 'pk', 'sets',
                          'set', 'units', 'unit', 'nos', 'order', 'all', 'in'})
_GRAND_WORDS = frozenset({'grand', 'g', 'overall', 'order'})


def total_kind(value):
    """'sub', 'grand' or 'total' for a label that names a total row, 'maybe' for a total word followed by other words
    ("TOTAL ECLIPSE": a colour, or a total), None for any other label."""
    if not isinstance(value, str):
        return None
    words = norm(value).split()
    i = next((k for k, w in enumerate(words) if w in _TOTAL_WORDS), None)
    if i is None:
        return None
    word, head, tail = words[i], words[:i], words[i + 1:]
    if tail and tail[0] not in ('of', 'for') and not set(tail) <= _TOTAL_AFTER:
        return 'maybe'
    if word.startswith('sub') or 'sub' in head:
        return 'sub'
    if word.startswith('g') or _GRAND_WORDS & set(head):
        return 'grand'
    return 'total'


def is_total(value):
    return total_kind(value) in ('sub', 'grand', 'total')


def is_subtotal(value):
    return total_kind(value) == 'sub'


def total_heading(value, fields=None):
    """A total or quantity column heading ("Total", "TTL", "Total Qty", "Pcs", "Qty (pcs)"): never a size."""
    if not isinstance(value, str):
        return False
    n = norm(value); qty = {norm(a) for a in F.resolve(fields).aliases('quantity')}
    return is_total(value) or n.startswith('total') or n in qty or (strip_units(n) != n and strip_units(n) in qty)


# Rows inside a size table that describe packing, not colour quantities: a size ratio / assortment ("Ratio", "Pack
# Ratio", "Assortment") or a pack / carton count ("No of Packs", "Cartons").
_RATIO = re.compile(r'\b(?:ratio|ratios|assortment|assort)\b')
_PACK_COUNT = re.compile(r'(?:(?:no|number|qty|quantity|total)\s+(?:of\s+)?)?(?:packs?|pks?|cartons?|ctns?|boxes)')


def packing_row(value):
    n = norm(value) if isinstance(value, str) else ''
    return 'ratio' if _RATIO.search(n) else 'packs' if n and _PACK_COUNT.fullmatch(n) else None


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
    if not mine:        # a sheet with no layout labels is not recognisably any buyer's layout
        return 0.0
    return 1.0 if signature_of(mine) == t['signature'] else similarity(mine, t['labels'])


def match_template(sheet, templates, threshold=0.8):
    best = None
    for t in templates:
        score = template_score(sheet, t)
        if score >= threshold and (best is None or score > best[0]):
            best = (score, t)
    return best


# --- cell helpers ----------------------------------------------------------------
MERGE_LOOKUP_AREA = 64      # merged headings are small; larger merged areas are not expanded for lookups


class Grid:
    """One sheet of the cell model for reading. Cells in hidden rows or columns are never read (they often hold
    an older revision); `hidden_rows` / `hidden_cols` name them so the order can say what was left out."""

    def __init__(self, sheet, fields=None):
        self.sheet = sheet
        self.fields = F.resolve(fields)
        self.cells = {(c['row'], c['col']): c for c in sheet['cells'] if not c.get('hidden')}
        hidden = [c for c in sheet['cells'] if c.get('hidden')]
        self.hidden = {(c['row'], c['col']) for c in hidden}
        rows = {c['row'] for c in sheet['cells'] if not c.get('hidden')}
        cols = {c['col'] for c in sheet['cells'] if not c.get('hidden')}
        self.hidden_cols = {c['col'] for c in hidden} - cols
        self.hidden_rows = {c['row'] for c in hidden if c['col'] not in self.hidden_cols} - rows
        self.rows = sheet['rows']; self.cols = sheet['cols']
        self._norms = {}; self._index = None      # normalised text per cell + label index, built once per sheet
        self._strips = {}; self._row_last = None  # label-strip rows, last filled column per row (built when asked)
        self.unclear = set()                      # quantity cells kept as text because the number is ambiguous
        self.merge_top = {}
        for rng in sheet.get('merged') or ():
            try:
                a, b = (cell_ref(x) for x in str(rng).split(':', 1))
            except ValueError:
                continue
            if (b[0] - a[0] + 1) * (b[1] - a[1] + 1) <= MERGE_LOOKUP_AREA:
                for r in range(a[0], b[0] + 1):
                    for c in range(a[1], b[1] + 1):
                        if (r, c) != a:
                            self.merge_top[(r, c)] = a

    def v(self, r, c):
        return self.cells.get((r, c), {}).get('value')

    def head_at(self, r, c):
        """(row, col) holding the heading shown at (r, c): the cell itself, or the top-left of its merged area."""
        return self.merge_top.get((r, c), (r, c)) if self.v(r, c) in (None, '') else (r, c)

    def head(self, r, c):
        """The heading shown at (r, c): a two-row header merges "Colour" over both rows."""
        return self.v(*self.head_at(r, c))

    def p(self, r, c):
        cell = self.cells.get((r, c), {})
        return source(self.sheet['name'], cell.get('cell', ''), cell.get('value'), cell.get('formula'))

    def unclear_at(self, r, c):
        """A number the reader could not read safely: text such as “2.000” (in a file that writes decimals with a
        comma the dot groups thousands, so it is 2000; elsewhere it may be 2). Kept as text for the person to type."""
        cell = self.cells.get((r, c)) or {}
        return cell.get('flag') == 'decimal_comma' or dotted(cell.get('value'))

    def err(self, r, c):
        """An Excel error value (#REF!, #N/A, #DIV/0!) in this cell: a formula that failed in the file."""
        cell = self.cells.get((r, c))
        return bool(cell) and (bool(cell.get('error')) or is_error(cell.get('value')))

    def shown(self, r, c):
        """The text of a cell as the file shows it: '5012.10' for a code the reader holds as the number 5012.1."""
        cell = self.cells.get((r, c)) or {}
        return cell['text'] if isinstance(cell.get('text'), str) else text(cell.get('value'))

    def last_col(self, r):
        """The last filled column of row r (0 for an empty row)."""
        if self._row_last is None:
            self._row_last = {}
            for (rr, cc) in self.cells:
                if cc > self._row_last.get(rr, 0):
                    self._row_last[rr] = cc
        return self._row_last.get(r, 0)

    def strip(self, r):
        """True when row r is a strip of labels with their values underneath (“PO No | Buyer Ref | Style” over
        “PO-4471 | BR-22 | ZK-2041”): every filled cell reads like a label and two or more have a value below."""
        if r not in self._strips:
            filled = [(c, self.v(r, c)) for c in range(1, self.last_col(r) + 1) if self.v(r, c) not in (None, '')]
            under = [c for c, _ in filled if self.v(r + 1, c) not in (None, '') and not reads_like_label(self, self.v(r + 1, c))]
            self._strips[r] = (len(filled) >= 2 and len(under) >= 2 and len(filled) <= 40
                               and all(reads_like_label(self, v) for _, v in filled))
        return self._strips[r]

    def qty(self, r, c, value=None):
        """The quantity in a cell as the order keeps it; an unclear number stays text (the planner then marks
        that cell red and the size table says why) instead of being read as the wrong number."""
        value = self.v(r, c) if value is None else value
        if self.unclear_at(r, c):
            self.unclear.add(address(c, r))
            return value
        return quantity_of(value)

    def unclear_note(self):
        if not self.unclear:
            return None
        cells = sorted(self.unclear, key=lambda a: cell_ref(a))
        shown = ', '.join(cells[:6]) + ('…' if len(cells) > 6 else '')
        example = text(self.v(*cell_ref(cells[0])))
        m = _QTY_TEXT.fullmatch(example); digits = m.group(1) if m else ''
        readings = f': {digits.replace(".", "")} or {float(digits):g}?' if _DOTTED.fullmatch(digits) else ''
        return (f'Quantities in {shown} on “{self.sheet["name"]}” are written with a dot between thousands or a decimal point '
                f'(for example “{example}”{readings}). They were not read as numbers — type each quantity as a whole number.')

    def nv(self, r, c):
        """Normalised text of a visible cell. Cached: header lookups read the same sheet once per field and per
        order, which made big files quadratic (2,000 lines took minutes) when each lookup re-normalised every cell."""
        n = self._norms.get((r, c))
        if n is None:
            n = self._norms[(r, c)] = norm(self.v(r, c))
        return n

    def find(self, aliases, rows=None, first=True):
        if self._index is None:
            self._index = {}
            for (r, c) in self.cells:
                n = self.nv(r, c)
                if n:
                    self._index.setdefault(n, []).append((r, c))
        names = {norm(a) for a in aliases}
        hits = sorted(p for name in names for p in self._index.get(name, ()) if rows is None or p[0] in rows)
        return (hits[0] if hits else None) if first else hits

    def hidden_note(self):
        """Plain note naming hidden rows / columns that hold data (they were not read), or None."""
        def spans(nums, word):
            nums = sorted(nums); out = []
            for n in nums:
                if out and n == out[-1][1] + 1:
                    out[-1][1] = n
                else:
                    out.append([n, n])
            text = ', '.join(word(a) if a == b else f'{word(a)}–{word(b)}' for a, b in out[:6])
            return text + ('…' if len(out) > 6 else '')
        parts = []
        if self.hidden_rows:
            parts.append(f'row{"s" if len(self.hidden_rows) > 1 else ""} {spans(self.hidden_rows, str)}')
        if self.hidden_cols:
            parts.append(f'column{"s" if len(self.hidden_cols) > 1 else ""} {spans(self.hidden_cols, lambda c: address(c, 1)[:-1])}')
        if not parts:
            return None
        one = len(self.hidden_rows) + len(self.hidden_cols) == 1
        return (f'Hidden {" and ".join(parts)} on “{self.sheet["name"]}” {"holds" if one else "hold"} data and '
                f'{"was" if one else "were"} not read. Unhide {"it" if one else "them"} in Excel and upload again if '
                f'{"it belongs" if one else "they belong"} to the order.')

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
        names = {key: [x for x in (norm(a) for a in aliases) if x] for key, aliases in spec.items()}
        for col in range(1, self.cols + 1):
            n = self.nv(row, col)
            if not n:
                continue
            for key, alias_names in names.items():
                if key in found:
                    continue
                if any(n == a or (len(a) >= 3 and n.startswith(a + ' ')) for a in alias_names):
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
# Headings of buyer POs that name no order field but are labels all the same (in a strip of labels).
HEADINGS = frozenset({'remark', 'remarks', 'note', 'notes', 'comment', 'comments', 'fabric', 'composition', 'gsm', 'fit', 'wash',
                      'country', 'currency', 'price', 'payment terms', 'mode', 'ship mode', 'port', 'agent', 'factory', 'vendor',
                      'supplier', 'origin', 'country of origin', 'terms', 'incoterms', 'category', 'product', 'collection'})


def looks_like_label(value, fields=None):
    """A neighbour that is itself a heading (e.g. "QTY(IN PACKS)") is not the value of a label."""
    n = norm(value)
    words = F.resolve(fields).label_words
    return isinstance(value, str) and (n in words or n in OTHER_LABELS or bool(re.search(r'\b(qty|quantity|price|total)\b', n)))


def reads_like_label(g, value):
    """Text that reads like a field label ("Buyer Ref", "Ex-Fty", "PO No:") rather than a value: no digits, and it
    names an order field or is a known heading."""
    if not isinstance(value, str):
        return False
    n = norm(value)
    if not n or len(n) > 40 or any(ch.isdigit() for ch in n):
        return False
    if looks_like_label(value, g.fields) or n in HEADINGS or value.strip().endswith(':'):
        return True
    from . import matcher
    return any(matcher.name_score(value, g.fields.aliases(k))[0] >= matcher.FLOOR for k in g.fields.header_keys)


def field_value(g, r, c, key):
    """A header value as the order keeps it: dates stay raw for iso_date (an Excel serial number is a
    date, not the text "46402"), whole numbers for number fields, a code as the file shows it (5012.10),
    trimmed text otherwise."""
    v = g.v(r, c); kind = g.fields.kind(key)
    if kind == 'date' and isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if kind == 'number':
        n = number(v)
        if n is not None:
            return int(n) if float(n).is_integer() else n
    return g.shown(r, c) if kind == 'code' else text(v)


EPOCH_1904 = date(1904, 1, 1)


def set_field(g, o, key, r, c):
    """A header field read from (r, c) with its source. A date typed as a plain number in a workbook that counts dates
    from 1904 (Mac Excel) is converted with that calendar: the same number is a date 1,462 days later than in 1900."""
    o[key] = field_value(g, r, c, key); o.setdefault('provenance', {})[key] = g.p(r, c)
    v = o[key]
    if g.fields.kind(key) == 'date' and g.sheet.get('date1904') and isinstance(v, (int, float)) and not isinstance(v, bool) \
            and 0 < v < 80000:
        o[key] = (EPOCH_1904 + timedelta(days=int(v))).isoformat()
        o['provenance'][key]['derivation'] = f'Excel date number {v:g} read as a date (this workbook counts dates from 1904)'


def label_value(g, r, c):
    """(row, col) of the value of the label at (r, c): the cell to its right — or the cell below it when the label is
    in a strip of labels with their values underneath — or None when that cell is itself a label."""
    t = ((r + 1, c) if g.v(r + 1, c) not in (None, '') else None) if g.strip(r) else g.right(r, c)
    return t if t and not looks_like_label(g.v(*t), g.fields) else None


def header_targets(g, key, limit_row):
    """[((label row, col), (value row, col))] for every label of this field above limit_row that has a value."""
    rows = range(1, max(2, limit_row))
    return [(hit, t) for hit in g.find(g.fields.aliases(key), rows, first=False) for t in [label_value(g, *hit)] if t]


def header_value(g, key, limit_row):
    """(row, col) of the value next to the first label of this field above limit_row, or None."""
    found = header_targets(g, key, limit_row)
    return found[0][1] if found else None


def _same_value(g, key, at):
    v = field_value(g, *at, key)
    if g.fields.kind(key) == 'date':
        return iso_date(v)[0] or canon(v)
    return canon(v)


def rivals(g, key, found):
    """The labels of one field that give different values ("Item | Baby Romper" and "Style No | ZK-2041"), or None.
    Which one is meant is the person's decision: it is asked, never picked."""
    seen = {}
    for hit, t in found:
        seen.setdefault(_same_value(g, key, t), (hit, t))
    return list(seen.values()) if len(seen) > 1 else None


def rival_text(g, key, found):
    return ' and '.join(f'{address(h[1], h[0])} “{text(g.v(*h))}” says “{g.shown(*t)}”' for h, t in found[:3])


def ask_rivals(g, o, key, found):
    """Two labels of one field with different values: the field is left empty with a question naming both cells."""
    name = CODE_NAMES.get(key) or g.fields.label(key).lower()
    hit, t = found[0]
    o[key] = ''
    o.setdefault('provenance', {})[key] = dict(g.p(*t), question=f'{rival_text(g, key, found)} — which is the {name}? Enter it.',
                                               doubt=[address(c, r) for _, (r, c) in found])
    review(o, key, f'{rival_text(g, key, found)} on “{g.sheet["name"]}” — two labels name the {name} with different values. '
                   f'Enter the {name} that is meant.')


def header_fields(g, o, limit_row, only_missing=False):
    """Key-value labels ("BUYER | NEXT") anywhere above the first data block; values under their labels in a strip
    of labels. Two labels of one field with different values are asked."""
    for key in g.fields.header_keys:
        if only_missing and o.get(key) not in (None, ''):
            continue
        found = header_targets(g, key, limit_row)
        if not found:
            continue
        rival = rivals(g, key, found)
        if rival:
            ask_rivals(g, o, key, rival)
        else:
            set_field(g, o, key, *found[0][1])


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


def unit_evidence(g, table, role=None, rows=None):
    """Decide qty_unit from what the file itself says next to the quantities: the quantity heading (flat),
    the total / left headings (matrix), quantities written with their unit in the rows the table read (`rows`;
    by default the rows under the header up to the first empty one — never a trims list further down), a unit
    label ("UOM | PCS") or a note just above the table.
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
        v = g.head(r, c); u, per = unit_words(v)
        if u:
            at = g.head_at(r, c)
            found.append(dict(unit=u, per=per, cell=address(at[1], at[0]), value=v, evidence=f'the {what} says “{text(v)}”'))
    # Quantities written with their unit ("1,500 pcs", "20 packs"): the cells say it themselves.
    qcols = ([int(table['columns']['quantity'])] if table.get('type') == 'flat' and (table.get('columns') or {}).get('quantity') else
             list(range(int(table['first_size_col']), int(table['last_size_col']) + 1)) if table.get('type') == 'matrix'
             and table.get('first_size_col') and table.get('last_size_col') else [])
    said_units = {}
    if rows is None:
        rows = []
        for r in range(hr + 1, min(hr + 501, g.rows + 1)) if hr and qcols else ():
            if all(g.v(r, c) in (None, '') for c in qcols):
                if rows:
                    break
                continue
            rows.append(r)
    for r in rows:
        for c in qcols:
            v = g.v(r, c)
            if isinstance(v, str):
                u = qty_value(v)[1]
                if u and u not in said_units:
                    said_units[u] = (r, c, v)
    for u, (r, c, v) in said_units.items():
        found.append(dict(unit=u, per=None, cell=address(c, r), value=v, evidence=f'the quantities say “{text(v)}” ({address(c, r)})'))
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
    if p.get('reason') == 'review' and ev:        # the person chose to decide on review: quote the file as it is
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
        end = next((x for x in range(r + 1, min(r + 101, g.rows + 1)) if is_total(g.v(x, gcol)) and not is_subtotal(g.v(x, gcol))), None)
        role = norm(g.v(r + 1, ccol))
        blocks.append(dict(header=r, end=end, gcol=gcol, ccol=ccol, tcol=tcol,
                           role=role if role in ('sample', 'summary') else 'order'))
    return blocks


def size_entry(g, header, r, c):
    """One size of a line: the heading in the header row, the quantity in row r (an unclear number stays text and is
    marked, so the planner refuses it and the size table says why)."""
    return dict(size=text(g.v(header, c)), quantity=g.qty(r, c), source=g.p(r, c), size_source=g.p(header, c),
                **({'unclear': text(g.v(r, c))} if g.unclear_at(r, c) else {}))


def matrix_lines(g, header, end, label_col, color_col, first_size, total_col):
    result = []
    for r in range(header + 1, end):
        if g.v(r, label_col) is None and g.v(r, color_col) is None or is_total(g.v(r, label_col)) or is_total(g.v(r, color_col)):
            continue
        result.append(dict(label=text(g.v(r, label_col)), color=text(g.v(r, color_col)),
            declared_total=g.qty(r, total_col) if total_col else None, provenance={'color': g.p(r, color_col)},
            sizes=[size_entry(g, header, r, c) for c in range(first_size, (total_col or first_size) if total_col else first_size + 1)
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
    if g.unclear_note():
        review(o, 'lines', g.unclear_note())
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
        if norm(cell['value']) != 'item number' or cell.get('hidden'):
            continue
        r, c = cell['row'], cell['col']; o = base_order(); g.unclear = set()
        def setval(k, rr, cc):
            o[k] = g.shown(rr, cc) if k in CODE_NAMES else text(g.v(rr, cc)); o['provenance'][k] = g.p(rr, cc)
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
        line = dict(label=o['style'], color='', declared_total=g.qty(data, 9), provenance={},
            sizes=[size_entry(g, header, data, col) for col in size_columns])
        o['order_lines'] = [line]; o['lines'] = deepcopy(o['order_lines'])
        o['warnings'] = ['Buyer contract quantities are not identified as sample quantities. Confirm the quantity basis and pieces per source unit.',
            'Buyer, colour and order confirmation date require review; destination is not assumed to be buyer.']
        set_unit(o, unit_evidence(g, dict(type='matrix', header_row=header, total_col=9, label_col=10), role='contract', rows=[data]))
        if g.unclear_note():
            review(o, 'lines', g.unclear_note())
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


CODE_NAMES = {'style': 'style number', 'po_number': 'PO number', 'order_ref': 'order reference'}


def reads_like_text(value):
    """A code cell holding a sentence ("BABY GIRLS ROMPER WITH FRILL AND HOOD") rather than a code."""
    t = text(value)
    return len(t.split()) >= 5 or len(t) > 40


def date_order(raw):
    """'dmy' or 'mdy' when a numeric d/m/y date can be read only one way (20/12/2026, 12/20/2026), else None."""
    m = re.fullmatch(r'\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*', raw) if isinstance(raw, str) else None
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return 'dmy' if a > 12 >= b else 'mdy' if b > 12 >= a else None


def month_first_example(raws):
    """A raw date that proves the file writes month first — only when no date in it is unmistakably day-first."""
    kinds = {date_order(r) for r in raws} - {None}
    return next((r for r in raws if date_order(r) == 'mdy'), None) if kinds == {'mdy'} else None


def _both_readings(raw):
    m = re.fullmatch(r'\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*', raw)
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3)); y += 2000 if y < 100 else 0
    names = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')
    return f'{a} {names[b - 1]} {y}', f'{b} {names[a - 1]} {y}'


def finish(o, month_first=None):
    """Dates to YYYY-MM-DD and the last checks on the values read. month_first: a raw date elsewhere in the
    file that can be read only month first ("12/20/2026"); an ambiguous date ("10/01/2026") is then not read
    day-first but left empty and asked. Without it, the order's own dates are the evidence."""
    o['workbook_pieces'] = sum(int(number(s.get('quantity')) or 0) for l in o.get('order_lines', []) for s in l.get('sizes', [])) or None
    month_first = month_first or month_first_example([o.get(k) for k in DATE_FIELDS])
    for key in DATE_FIELDS:
        raw = o.get(key)
        if raw in (None, ''):
            continue
        iso, note = iso_date(raw)
        name = key.replace('_', ' ')
        if iso and iso != raw and note and 'day/month' in note and month_first:
            first, second = _both_readings(raw)
            # the short question is shown on the field; the note says why
            o.setdefault('provenance', {}).setdefault(key, {}).update(
                question=f'“{raw}” could be {first} or {second} — enter the {name}.', derivation=f'"{raw}" not converted: day and month are unclear')
            o.setdefault('warnings', []).append(f'{name.capitalize()}: “{raw}” could be {first} or {second}; this file writes “{month_first}” '
                                                f'month first — enter the {name}.')
            o[key] = ''
        elif iso and iso != raw:
            o[key] = iso
            o.setdefault('provenance', {}).setdefault(key, {})['derivation'] = note or f'"{raw}" converted to {iso}'
            if note and 'day/month' in note:          # shown on the field and under "Please read before approving"
                review(o, key, f'{name.capitalize()}: {note} ({iso}) — check it.')
        elif not iso:
            o.setdefault('warnings', []).append(f'{name.capitalize()} "{raw}" is not a date this product can read — enter it.')
            o[key] = ''
    for key, name in CODE_NAMES.items():
        v = o.get(key)
        if isinstance(v, str) and reads_like_text(v):
            # e.g. a "Style" column that holds descriptions: never created as a new style number
            p = o.setdefault('provenance', {}).setdefault(key, {})
            where = f'{p["cell"]} ' if p.get('cell') else 'The file '
            p.update(question=f'The file has a description here (“{v[:24].rstrip()}…”), not a {name} — enter the {name}.', doubt=v)
            o.setdefault('warnings', []).append(f'{where}holds “{v[:60]}{"…" if len(v) > 60 else ""}”, which reads like a description, '
                                                f'not a {name} — enter the {name}.')
            o[key] = ''
    return o


def parse(content, templates=(), filename='', fields=None):
    fmt, sheets = readers.read(content, filename)
    out = parse_sheets(sheets, templates, fields)
    out['source_format'] = readers.LABELS[fmt]
    return out


def flat_headers(g):
    """(header row, {field: column}, {field: [columns]}) for each row that heads a one-row-per-size table: size
    and quantity headings (a quantity heading may carry its unit, "Qty (pcs)"); style is a column or a header
    field. The third item names fields headed by two or more columns ("Order Qty" and "Sample Qty"): such a row
    is never read automatically, because which column is meant is the person's decision."""
    Fv = g.fields; names = {k: {norm(a) for a in Fv.aliases(k)} for k in Fv.column_keys}
    quantity = names.get('quantity', set())
    for row in range(1, min(g.rows, 80) + 1):
        cells = [(c, norm(g.v(row, c))) for c in range(1, g.cols + 1) if isinstance(g.v(row, c), str)]
        hits = {}
        for c, n in cells:
            for key in Fv.column_keys:
                if n in names[key] or (key == 'quantity' and strip_units(n) != n and strip_units(n) in quantity):
                    hits.setdefault(key, []).append(c)
        hit = {k: cs[0] for k, cs in hits.items()}
        if {'size', 'quantity'} <= hit.keys():
            yield row, hit, {k: cs for k, cs in hits.items() if len(cs) > 1}


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
    formats = []; used = []; notes = []; refusals = []; origin = []
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
            # The first size/quantity table with a style is read, or refused with the reason; after a refusal no
            # later table on the sheet is read instead (which table is the order is then the person's decision).
            for row, cols, dups in flat_headers(g):
                lead = f'The size/quantity table on “{sheet["name"]}” row {row} was not read automatically: '
                if dups:
                    mine.append(lead + '; '.join(
                        ' and '.join(f'{address(c, row)} “{text(g.v(row, c))}”' for c in cs) + f' both name the {Fv.label(k).lower()} column'
                        for k, cs in dups.items()) + ' — choose the column to use.')
                    break
                d = dict(fields={}, table=dict(type='flat', header_row=row, columns=cols))
                if 'style' not in cols:
                    targets = header_targets(g, 'style', row)
                    if not targets:
                        continue
                    rival = rivals(g, 'style', targets)
                    if rival:
                        mine.append(lead + rival_text(g, 'style', rival) + ' — both name the style; choose the cell to use.')
                        break
                    at = targets[0][1]
                    d['fields']['style'] = address(at[1], at[0])
                _, rejected = matcher.check(sheet, d, Fv)
                if rejected:
                    mine.append(lead + '; '.join(r['reason'] for r in rejected) + '.')
                    break
                try:
                    found = apply_template(sheets, sheet, d, Fv)
                except ValueError as exc:          # e.g. a line without a style: asked on the mapping screen
                    mine.append(lead + str(exc).rstrip('.') + '.')
                    found = None
                    break
                formats.append('Tabular orders')
                for o in found:
                    header_fields(g, o, row, only_missing=True)
                break
        if found:
            for o in found:
                o['warnings'] += [n for n in mine if n not in o['warnings']]
            attach_bom(g, found)
        mine += [n for n in read_checks(sheet, found or [], Fv, g) if n not in mine]
        notes += [n for n in mine if n not in notes]
        origin += found or []
    orders, unread = finish_orders(sheets, origin)
    notes += [n for n in unread if n not in notes]
    return dict(sheets=sheets, orders=orders, format=', '.join(dict.fromkeys(formats)) or 'Needs column mapping',
                templates_used=used, notes=notes, templates_refused=refusals)


def read_checks(sheet, orders, fields=None, g=None):
    """The checks every reading of a sheet ends with — read automatically, with a saved layout or with a layout a
    person mapped (POST /map): hidden rows and columns that hold data, a second date label, Excel error values read
    as values, formulas without a calculated result and what this file's reader could not see. What the approver
    must see becomes review items on the orders (each order also remembers its sheet). Returns the draft notes."""
    g = g or Grid(sheet, fields); name = sheet['name']
    hidden = g.hidden_note()
    notes = ([hidden] if hidden else []) + list(readers.limitations(sheet))
    uncalculated = [c['cell'] for c in sheet['cells'] if c.get('formula') and c.get('value') is None]
    stale = (f'{len(uncalculated)} formula cells on "{name}" have no calculated value (e.g. {", ".join(uncalculated[:4])}). '
             'Open the file in Excel, save it, and upload again.') if uncalculated else None
    for o in orders:
        o['sheet'] = name
        if stale:
            if stale not in o['warnings']:
                o['warnings'].insert(0, stale)    # files saved by tools other than Excel can lack calculated results
            review(o, 'lines', stale)
        if hidden:
            review(o, 'lines', hidden)
        rival_dates(g, o)
        error_values(g, o)
        reader_gaps(sheet, o)
    return notes


def error_values(g, o):
    """Excel error values (#REF!, #N/A, #DIV/0!) read as a value — a formula that failed in the buyer's file: a field
    or colour is left empty with a question naming the cell; a quantity keeps the error text, which the planner
    refuses; the size table names the cells."""
    sheet = g.sheet['name']; prov = o.setdefault('provenance', {})

    def bad(p):
        if not isinstance(p, dict) or not p.get('cell') or p.get('sheet') not in (None, sheet):
            return None
        try:
            r, c = cell_ref(p['cell'])
        except ValueError:
            return None
        return text(g.v(r, c)) if g.err(r, c) else None
    for key in g.fields.header_keys:
        v = bad(prov.get(key))
        if v and v in text(o.get(key)):
            name = CODE_NAMES.get(key) or g.fields.label(key).lower(); cell = prov[key]['cell']
            o[key] = ''
            prov[key].update(question=f'{cell} holds the Excel error “{v}”, not a {name} — enter the {name}.', doubt=v)
            review(o, key, f'{cell} on “{sheet}” holds the Excel error “{v}” (a formula that failed in the file) instead of the {name}. '
                           f'Fix it in the file and upload again, or enter the {name}.')
    cells = set()
    for key in ('lines', 'order_lines', 'sample_lines'):
        for line in o.get(key) or []:
            lp = line.get('provenance') or {}
            for part in ('color', 'label'):
                if bad(lp.get(part)):
                    line[part] = ''; cells.add(lp[part]['cell'])
            if is_error(line.get('label')):
                line['label'] = ''
            for s in line.get('sizes') or []:
                if bad(s.get('size_source')):
                    s['size'] = ''; cells.add(s['size_source']['cell'])
                if bad(s.get('source')):
                    cells.add(s['source']['cell'])
    if cells:
        cells = sorted(cells, key=cell_ref)
        review(o, 'lines', f'{", ".join(cells[:6])}{"…" if len(cells) > 6 else ""} on “{sheet}” hold{"s" if len(cells) == 1 else ""} '
                           'Excel errors (formulas that failed in the file), not values. Fix them in the file and upload again, or type the values.')


def reader_gaps(sheet, o):
    """What this file's reader could not see (an .xlsb, or a workbook only the backup reader could open), as review items
    on what it may have changed: codes that lost their leading zeros, rows the buyer hid, merged style cells."""
    caps = sheet.get('capabilities') or {}; name = sheet['name']
    if caps.get('number_formats') is False:
        for key, label in CODE_NAMES.items():
            v = text(o.get(key))
            if v.isdigit():
                review(o, key, f'The {label} “{v}” was read from “{name}” without its cell format, so leading zeros (such as “00{v}”) '
                               'may be missing — check it against the file.')
    if caps.get('hidden_rows') is False or caps.get('hidden_cols') is False:
        review(o, 'lines', f'Hidden rows and columns of “{name}” cannot be seen in this file as it was read, so rows or columns the buyer '
                           'hid were read like the others — compare the size table with the file.')
    if caps.get('merged') is False:
        review(o, 'lines', f'Merged cells of “{name}” cannot be seen in this file as it was read, so a style or colour written once over '
                           'several rows was read on its first row only — compare the size table with the file.')


def sheet_of(o):
    """The sheet an order was read from (orders saved before they remembered it: the sheet of their sources)."""
    if o.get('sheet'):
        return o['sheet']
    for p in (o.get('provenance') or {}).values():
        if isinstance(p, dict) and p.get('sheet'):
            return p['sheet']
    for line in o.get('lines') or []:
        for s in line.get('sizes') or []:
            if (s.get('source') or {}).get('sheet'):
                return s['source']['sheet']
    return None


def has_data(sheet):
    """A sheet holding quantities (three or more numbers in visible cells), which may hold an order."""
    n = 0
    for c in sheet['cells']:
        if not c.get('hidden') and qty_value(c['value'])[0] is not None:
            n += 1
            if n >= 3:
                return True
    return False


UNREAD = 'No order was read from sheet “'


def unread_note(name):
    return f'{UNREAD}{name}”, which holds data. If it belongs to this order, map that sheet on the layout screen.'


def forget(o, message):
    o['warnings'] = [w for w in o.get('warnings') or [] if w != message]
    o['review'] = [i for i in o.get('review') or [] if i['message'] != message]


def finish_orders(sheets, orders):
    """The last checks across all orders of a draft (after an upload, and again after a layout is mapped): one date
    convention per file, the same order on two sheets, and visible sheets with data that gave no order (a draft note
    and a review item on every order, so nobody approves sheet 1 without knowing sheet 2 was not read).
    Returns (orders, notes)."""
    month_first = month_first_example([o.get(k) for o in orders for k in DATE_FIELDS])
    orders = [finish(o, month_first) for o in orders]
    same_order_on_two_sheets([(sheet_of(o), o) for o in orders])
    read = {sheet_of(o) for o in orders}
    unread = [s['name'] for s in sheets if not s['hidden'] and s['name'] not in read and has_data(s)]
    # One sheet and no order: the layout screen asks anyway. Otherwise every sheet left out is named.
    notes = [unread_note(n) for n in unread] if orders or len(unread) > 1 else []
    for o in orders:
        for item in list(o.get('review') or []):
            if item['message'].startswith(UNREAD) and item['message'] not in notes:
                forget(o, item['message'])            # that sheet has been mapped since
        for n in notes:
            review(o, 'lines', n)
    return orders, notes


def review(o, field, message):
    """A reading finding the person must see before approving (a second date, a total that disagrees, rows
    left below a table, hidden data, the same order twice). It stays in the order's notes and the planner
    raises it as a warning on the field it concerns ('lines' = the size table), so it is shown on the review
    screen and in "Please read before approving"."""
    o.setdefault('warnings', [])
    if message not in o['warnings']:
        o['warnings'].append(message)
    items = o.setdefault('review', [])
    if not any(i['message'] == message for i in items):
        items.append(dict(field=field, message=message))


def rival_dates(g, o):
    """Another label that also names a date of this order ("Revised Ship Date", "Delivery Date 2", or both
    "Ship Date" and "ETD") with a different date next to it: the order says which cell it read and asks."""
    for key in DATE_FIELDS:
        chosen = ((o.get('provenance') or {}).get(key) or {}).get('cell')
        mine = iso_date(o.get(key))[0]
        if not chosen or not mine:
            continue
        phrases = [n for n in (norm(a) for a in g.fields.aliases(key)) if n]
        for cell in sorted(g.cells.values(), key=lambda x: (x['row'], x['col'])):
            n = norm(cell['value']) if isinstance(cell['value'], str) and cell['row'] <= 60 else ''
            if not n or not any(f' {a} ' in f' {n} ' for a in phrases):
                continue
            t = label_value(g, cell['row'], cell['col'])
            if not t or address(t[1], t[0]) == chosen:
                continue
            other = iso_date(g.v(*t))[0]
            if other and other != mine:
                review(o, key, f'{cell["cell"]} “{text(cell["value"])}” says {other}, but the {key.replace("_", " ")} was read from {chosen} '
                               f'({mine}) — check which date is meant.')


def same_order_on_two_sheets(origin):
    """The same style and PO read from two sheets (often "PO rev1" and "PO rev2"): both orders say so."""
    seen = {}
    for sheet, o in origin:
        if o.get('style'):
            seen.setdefault((canon(o['style']), canon(o.get('po_number'))), []).append((sheet, o))
    for (_, po), items in seen.items():
        names = list(dict.fromkeys(s for s, _ in items))
        if len(names) > 1:
            first = items[0][1]
            note = (f'Sheets {" and ".join(f"“{s}”" for s in names)} both hold style {first["style"]}'
                    + (f' and PO {first["po_number"]}' if po else '') + ' — if one is an older copy, remove that order before saving.')
            for _, o in items:
                review(o, 'style', note)


def canon(value):
    """Grouping form of a key value: 'Zen Kids' and 'ZEN KIDS ' are the same buyer (the masters agree)."""
    return ' '.join(text(value).upper().split())


def _below_note(g, stop, qcols, after=None):
    """Rows under the end of a table that still hold quantities: named, never added to the order. after: the row of
    the grand total that ended the table (the rows right below it are checked)."""
    if after:
        stop = after + 1
    if not stop or not qcols:
        return None
    rows = []; width = range(1, max(qcols) + 1)
    for r in range(stop, min(stop + 500, g.rows + 1)):
        if (r > stop or after) and all(g.v(r, c) in (None, '') for c in width):
            break                                      # this block ends; tables further down are other tables
        if r not in g.hidden_rows and any(qty_value(g.v(r, c))[0] is not None for c in qcols):
            rows.append(r)
    if not rows:
        return None
    span = f'row {rows[0]}' if len(rows) == 1 else f'rows {rows[0]}–{rows[-1]}'
    if after:
        return (f'The table on “{g.sheet["name"]}” ends with the grand total in row {after}; quantities in {span} below it were '
                'not read. Map them separately if they belong to the order.')
    first = next((text(g.v(stop, c)) for c in range(1, g.cols + 1) if g.v(stop, c) not in (None, '')), '')
    return (f'The table on “{g.sheet["name"]}” ends at row {stop}' + (f' (“{first[:40]}”)' if first else '') +
            f'; quantities in {span} below it were not read. Map them separately if they belong to the order.')


class _Blocks:
    """The quantities a table read per block — the lines since the last total row — and in all, so that every total
    row (a "Total" under each colour block, sub totals, a grand total) is checked against the rows it adds up."""

    def __init__(self, g):
        self.g = g; self.notes = []; self.all = 0; self.totals = 0; self.last_total = None; self.any_unclear = False
        self._new()

    def _new(self):
        self.block = 0; self.cols = {}; self.rows = []; self.unclear = False

    def line(self, r, quantities, unclear=False):
        for c, q in quantities.items():
            self.cols[c] = self.cols.get(c, 0) + q
        q = sum(quantities.values()); self.block += q; self.all += q; self.rows.append(r)
        self.unclear |= unclear; self.any_unclear |= unclear

    def sums(self, quantities):
        """True when a row's quantities are, size by size, the sums of the lines of the block above it."""
        return bool(self.rows) and bool(quantities) and all(
            abs(self.cols.get(c, 0) - quantities.get(c, 0)) < 1e-9 for c in set(self.cols) | set(quantities))

    def total(self, r, label, q, kind):
        """A total row: compared with its block (a grand total, the first total and a total right under another
        one with the whole table read so far; a running total that equals the whole table is fine too)."""
        whole = kind == 'grand' or not self.totals or not self.rows
        expected = self.all if whole else self.block
        unclear = self.any_unclear if whole else self.unclear
        if q is not None and not unclear and abs(q - expected) >= 1e-9 and (whole or abs(q - self.all) >= 1e-9):
            where = ('the rows read add up to' if whole else f'row {self.rows[0]} above it adds up to' if len(self.rows) == 1
                     else f'rows {self.rows[0]}–{self.rows[-1]} above it add up to')
            self.notes.append(f'Row {r}{f" (“{label}”)" if label else ""} on “{self.g.sheet["name"]}” says the total is {q:g}, but '
                              f'{where} {expected:g} — check for missing, hidden or extra rows before saving.')
        self.totals += 1; self.last_total = r; self._new()

    def finish(self):
        """Lines after the last total row with no total of their own (in a table that has total rows): named."""
        if self.totals and self.rows:
            one = len(self.rows) == 1
            span = f'Row {self.rows[0]}' if one else f'Rows {self.rows[0]}–{self.rows[-1]}'
            self.notes.append(f'{span} on “{self.g.sheet["name"]}” {"comes" if one else "come"} after the total in row {self.last_total} '
                              f'and {"has" if one else "have"} no total of {"its" if one else "their"} own — check that '
                              f'{"it belongs" if one else "they belong"} to the order.')
        return self.notes


def extra_cells(g, r, width):
    """Columns of a text-file row beyond the last heading: an unquoted “1,200” in a CSV splits into 1 and 200."""
    if g.sheet.get('reader') != 'csv' or not width:
        return []
    return [c for c in range(width + 1, g.last_col(r) + 1) if g.v(r, c) not in (None, '')]


def _split_note(g, split):
    if not split:
        return None
    r, extra = split[0]
    cells = ', '.join(f'{address(c, r)} “{text(g.v(r, c))}”' for c in extra[:3])
    rows = ', '.join(str(x) for x, _ in split[:6]) + ('…' if len(split) > 6 else '')
    many = len(split) > 1
    return (f'Row{"s" if many else ""} {rows} on “{g.sheet["name"]}” {"have" if many else "has"} more values than headings '
            f'({cells} under no heading): a number written with a comma, such as “1,200”, may have been split in this text file. '
            f'Type the quantit{"ies of these rows" if many else "y of this row"} as whole numbers.')


def parse_mapping(sheets, mapping, fields=None):
    """Orders from a one-row-per-size table (mapping: sheet, header_row, columns, and optionally fixed = {field:
    cell} for a value written once above the table, such as the style). The table ends where it ends: total
    rows (labelled, or an unlabelled row of the block's sum) are left out and each is checked against the rows it
    adds up, a grand total ends the table, hidden rows are not read, and a row that is not an order line (a title,
    a note, the headings of a second table) ends it — quantities below are named in the order's notes, never added.
    A text-file row with more values than headings has its quantity marked unclear (an unquoted 1,200 splits)."""
    Fv = F.resolve(fields)
    sheet = next((s for s in sheets if s['name'] == mapping['sheet']), None)
    if not sheet:
        raise ValueError('Select a worksheet.')
    row = int(mapping['header_row']); columns = dict(mapping['columns']); fixed = dict(mapping.get('fixed') or {})
    if not {'size', 'quantity'} <= columns.keys() or not ('style' in columns or 'style' in fixed):
        raise ValueError('Map style, size and quantity columns at minimum.')
    if any(k not in Fv.column_keys or not isinstance(c, int) or c < 1 or c > sheet['cols'] for k, c in columns.items()):
        raise ValueError('Invalid column mapping.')
    g = Grid(sheet, Fv); groups = {}; stop = ended = None; blocks = _Blocks(g); split = []
    heads = {k: norm(g.v(row, c)) for k, c in columns.items()}
    constant = {k: cell_ref(ref) for k, ref in fixed.items()}
    qcol = columns['quantity']; width = max((c for c in range(1, g.last_col(row) + 1) if g.v(row, c) not in (None, '')), default=0)

    def cell(r, k):
        """(value, (row, col)) of a mapped column; a merged style/colour block covering this row gives its value."""
        c = columns[k]; v = g.v(r, c)
        top = g.merge_top.get((r, c))
        if v in (None, '') and k not in ('size', 'quantity') and top and top[0] > row:
            return g.v(*top), top
        return v, (r, c)

    for r in range(row + 1, g.rows + 1):
        if r in g.hidden_rows:
            continue                                   # named by the sheet's hidden-rows note
        got = {k: cell(r, k) for k in columns}
        vals = {k: v for k, (v, _) in got.items()}
        if all(v in (None, '') for v in vals.values()):
            continue
        size, qty = vals.get('size'), vals.get('quantity')
        unclear = g.unclear_at(r, qcol); q = None if unclear else qty_value(qty)[0]
        names = [v for v in vals.values() if isinstance(v, str)] + [g.v(r, c) for c in range(1, min(g.cols, 3) + 1) if isinstance(g.v(r, c), str)]
        label = size if is_total(size) else next((x for x in names if is_total(x)), None)
        if label is None and size in (None, ''):
            label = next((x for x in names if total_kind(x)), None)
        if label is None and size in (None, '') and q is not None and blocks.rows and abs(q - blocks.block) < 1e-9 \
                and all(v in (None, '') for k, v in vals.items() if k != 'quantity'):
            label = ''                                 # an unlabelled row holding the sum of the block above: a total
        if label is not None:                          # TOTAL / Sub Total rows add up other rows
            kind = total_kind(label) if label else 'total'
            blocks.total(r, text(label), q, kind if kind in ('sub', 'grand') else 'total')
            if kind == 'grand':
                ended = r; break                       # the grand total ends the table
            continue
        if sum(1 for k, v in vals.items() if isinstance(v, str) and heads.get(k) and norm(v) == heads[k]) >= 2:
            stop = r; break                            # the headings again: a second table starts here
        if size in (None, '') and qty in (None, ''):
            stop = r; break                            # a title or a note, not an order line: the table has ended
        for k, at in constant.items():
            got.setdefault(k, (g.v(*at), at)); vals.setdefault(k, g.v(*at))
        # Blank styles cannot safely be forward-filled across unrelated sections.
        if not vals.get('style'):
            raise ValueError(f"{sheet['name']} row {r}: style is missing. Fill it or choose the correct header.")
        shown = {k: (g.shown(*got[k][1]) if k in CODE_NAMES else text(v)) for k, v in vals.items()}
        key = tuple(canon(shown.get(k)) for k in ('buyer', 'style', 'po_number', 'order_ref', 'ship_date'))
        o = groups.setdefault(key, base_order())
        for k in vals:
            if k not in ('size', 'quantity', 'color'):
                if o.get(k) and canon(o[k]) != canon(shown[k]):
                    raise ValueError(f'Conflicting {k} in row {r}.')
                if o.get(k):
                    continue                           # the first spelling is kept
                o[k] = shown[k]
                o['provenance'][k] = g.p(*got[k][1])
        color = shown.get('color', '')
        line = next((x for x in o['lines'] if canon(x['color']) == canon(color)), None)
        if line is None:
            line = dict(label=o['style'], color=color, declared_total=None, sizes=[], provenance={}); o['lines'].append(line)
            if 'color' in columns:
                line['provenance']['color'] = g.p(*got['color'][1])
        extra = extra_cells(g, r, width)
        if extra:                                      # the quantity may be only the first part of “1,200”
            joined = ','.join([text(qty)] + [text(g.v(r, c)) for c in extra]) if qcol == width else text(qty)
            line['sizes'].append(dict(size=text(size), quantity=joined, source=g.p(r, qcol), unclear=joined))
            split.append((r, extra)); blocks.line(r, {}, unclear=True)
            continue
        line['sizes'].append(dict(size=text(size), quantity=g.qty(r, qcol, qty), source=g.p(r, qcol),
                                  **({'unclear': text(qty)} if unclear else {})))
        blocks.line(r, {qcol: q or 0}, unclear)
    notes = blocks.finish() + [n for n in (_below_note(g, stop, [qcol], ended), g.unclear_note(), _split_note(g, split)) if n]
    for o in groups.values():
        o['warnings'] = ['Confirm whether the source quantities represent pieces or packs, and whether they are sample quantities.']
        for n in notes:
            review(o, 'lines', n)
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


NOT_SIZE = re.compile(r'\b(?:price|rate|fob|cost|amount|value|usd|inr|eur|gbp|weight|kgs?|cbm|ctns?|cartons?|gsm|remarks?)\b')


def matrix_orders(g, t):
    """Orders from a size matrix (sizes across columns). Every total row is left out and checked against the rows it
    adds up — a "Total" under each colour block, sub totals, a grand total, an unlabelled row of the block's sums;
    the table ends at its grand total, at an empty row, or at the first row without quantities under the sizes (a
    row with text there, and quantities below the end, are named). Ratio and pack-count rows are not lines. Hidden
    rows are not read; empty rows right under the headings (a header merged over two rows) are skipped. A style
    column gives one order per style (a style merged down over its colour rows belongs to each of them)."""
    from . import matcher
    header = int(t['header_row']); first, last = int(t['first_size_col']), int(t['last_size_col'])
    label_col = int(t.get('label_col') or 0) or None; color_col = int(t.get('color_col') or 0) or None
    total_col = int(t.get('total_col') or 0) or None
    cols = [c for c in range(first, last + 1) if g.v(header, c) is not None]
    heads = {c: norm(g.v(header, c)) for c in cols}
    sheet = g.sheet['name']; width = g.last_col(header)
    blocks = _Blocks(g); notes = []; packing = []; lines = []; rows = []; stop = ended = None; after_total = False

    def at(r, c):
        """(value, (row, col)) of a label column; a style or colour merged down over several rows gives its value."""
        if not c:
            return None, None
        top = g.merge_top.get((r, c))
        if g.v(r, c) in (None, '') and top and top[0] > header:
            return g.v(*top), top
        return g.v(r, c), (r, c)

    for r in range(header + 1, min(header + 201, g.rows + 1)):
        if r in g.hidden_rows:
            continue                                   # named by the sheet's hidden-rows note
        names = [g.v(r, c) for c in range(1, first) if isinstance(g.v(r, c), str)]
        kinds = [total_kind(x) for x in names]
        unclear = any(g.unclear_at(r, c) for c in cols)
        numbers = {c: q for c in cols if not g.unclear_at(r, c) for q in [qty_value(g.v(r, c))[0]] if q is not None}
        definite = next((x for x, k in zip(names, kinds) if k in ('sub', 'grand', 'total')), None)
        if definite is not None:
            q = None if unclear else sum(numbers.values()) if numbers else \
                (qty_value(g.v(r, total_col))[0] if total_col and not g.unclear_at(r, total_col) else None)
            kind = total_kind(definite); blocks.total(r, text(definite), q, kind)
            if kind == 'grand':
                ended = r; break                       # the grand total ends the table
            after_total = True; continue
        if all(g.v(r, c) in (None, '') for c in cols):
            if not names and not lines and not blocks.totals:
                continue                               # empty rows under the headings (a header merged over two rows)
            stop = r if names else None                # a title or a colour without quantities ends the table: named below
            break
        if sum(1 for c in cols if heads[c] and norm(g.v(r, c)) == heads[c]) >= 2:
            stop = r; break                            # the size headings again: a second table starts here
        if after_total and not numbers and not unclear:
            stop = r; break                            # text under the sizes after a total row: another table
        label, label_at = at(r, label_col); colour, colour_at = at(r, color_col)
        packed = next((x for x in [colour, label] + names if packing_row(x)), None)
        if packed is not None:
            packing.append((r, text(packed), packing_row(packed))); continue
        maybe = next((x for x, k in zip(names, kinds) if k == 'maybe'), None)
        if (not names or maybe is not None) and not unclear and blocks.sums(numbers):
            if not names or len(blocks.rows) >= 2:     # the sums of the rows above: a total row without (or with an odd) label
                if not names and len(blocks.rows) == 1:
                    notes.append(f'Row {r} on “{sheet}” has no colour and repeats the quantities of row {blocks.rows[0]}, so it was '
                                 'read as a total row, not a second line — check it.')
                blocks.total(r, text(maybe) if maybe else '', sum(numbers.values()), 'total')
                after_total = True; continue
            notes.append(f'Row {r} (“{text(maybe)}”) on “{sheet}” repeats the quantities of row {blocks.rows[0]}; it was read as a '
                         'colour, not a total row — check it.')
        if extra_cells(g, r, width):
            raise ValueError(f'Row {r} of “{sheet}” has more values than headings: a number written with a comma, such as “1,200”, '
                             'may have been split in this text file. Save the file from Excel with quotes around such numbers, '
                             'or as .xlsx, and upload again.')
        prov = {}
        if colour_at:
            prov['color'] = g.p(*colour_at)
        if label_at and label not in (None, ''):
            prov['label'] = g.p(*label_at)
        lines.append(dict(label=g.shown(*label_at) if 'label' in prov else '', color=text(colour),
                          declared_total=g.qty(r, total_col) if total_col else None, provenance=prov,
                          sizes=[size_entry(g, header, r, c) for c in cols]))
        rows.append(r); blocks.line(r, numbers, unclear); after_total = False
    notes = blocks.finish() + notes
    below = _below_note(g, stop, cols, ended)
    if below:
        notes.append(below)
    for c in (last + 1, first - 1):                    # a heading right next to the sizes with numbers under it
        if c < 1 or c in (total_col, label_col, color_col):
            continue
        h = g.head(header, c)
        if h in (None, '') or total_heading(h, g.fields) or (isinstance(h, str) and NOT_SIZE.search(norm(h))):
            continue
        if any(qty_value(g.v(r, c))[0] is not None for r in rows):
            span = f'{address(first, 1)[:-1]}–{address(last, 1)[:-1]}'
            notes.append(f'{address(c, header)} “{text(h)}”, right next to the size columns {span} on “{sheet}”, has numbers under it '
                         'but was not read as a size. If it is a size, map it with the other sizes; otherwise check that no quantity is missing.')
    if g.unclear_note():
        notes.append(g.unclear_note())
    unit_notes = [f'Row {r} (“{label}”) on “{sheet}” ' + ('is a size ratio (how a pack is made up), not quantities' if kind == 'ratio'
                  else 'counts packs or cartons, not pieces per size') + ', so it was not read as a line. Say whether the quantities '
                  'are pieces or packs, and how many pieces one pack holds.' for r, label, kind in packing]
    style_col = bool(label_col) and isinstance(g.head(header, label_col), str) and \
        matcher.name_score(g.head(header, label_col), g.fields.aliases('style'))[0] >= matcher.HIGH
    groups = {}
    for r, line in zip(rows, lines):
        groups.setdefault(canon(line['label']) if style_col and any(x['label'] for x in lines) else None, []).append((r, line))
    orders = []
    for key, items in groups.items() or [(None, [])]:
        ls = [line for _, line in items]
        o = base_order(); o['lines'] = ls; o['order_lines'] = deepcopy(ls); o['warnings'] = []
        if key:
            o['style'] = ls[0]['label']; o['provenance']['style'] = dict(ls[0]['provenance']['label'])
        elif key == '':
            where = ', '.join(str(r) for r, _ in items[:6]) + ('…' if len(items) > 6 else '')
            msg = (f'Row{"s" if len(items) > 1 else ""} {where} on “{sheet}” {"have" if len(items) > 1 else "has"} no style in column '
                   f'{address(label_col, 1)[:-1]}, while other rows have one — enter the style.')
            o['provenance']['style'] = dict(question=msg); review(o, 'style', msg)
        for n in notes:
            review(o, 'lines', n)
        for n in unit_notes:
            review(o, 'qty_unit', n)
        orders.append(o)
    return orders


def apply_template(sheets, sheet, d, fields=None):
    Fv = F.resolve(fields)
    validate_definition(sheet, d, Fv)
    g = Grid(sheet, Fv); fields = d.get('fields') or {}; t = d['table']
    if t['type'] == 'flat':
        cols = {k: int(v) for k, v in t['columns'].items()}
        fixed = {} if 'style' in cols else {'style': fields['style']}    # a style written once above the table
        orders = parse_mapping([sheet], dict(sheet=sheet['name'], header_row=int(t['header_row']), columns=cols, fixed=fixed), Fv)
    else:
        orders = matrix_orders(g, t)
    if not any(line.get('sizes') for o in orders for line in o.get('lines') or []):
        raise ValueError(f'No order lines were found under the headings in row {t["header_row"]} of “{sheet["name"]}”. '
                         'Check the header row and the size columns of the layout.')
    rows = sorted({cell_ref(s['source']['cell'])[0] for o in orders for line in o['lines'] for s in line['sizes']
                   if (s.get('source') or {}).get('cell')})
    unit = unit_evidence(g, t, rows=rows)             # only the rows this table read, never a trims list further down
    for o in orders:
        for k, ref in fields.items():
            r, c = cell_ref(ref)
            mine = (o['provenance'].get(k) or {}).get('cell')
            if k == 'style' and o.get('style') and mine and mine != address(c, r) and canon(g.shown(r, c)) != canon(o['style']):
                review(o, 'style', f'The style column gives {o["style"]}, but {address(c, r)} says “{g.shown(r, c)}” — check which style is meant.')
                continue                               # the style of these lines, read from the style column, is kept
            set_field(g, o, k, r, c)
        review_notes(set_unit(o, deepcopy(unit)))
    return orders
