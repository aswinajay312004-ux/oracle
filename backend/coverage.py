"""Extraction coverage manifest: what of the uploaded workbook was read into orders, and what was not.

Zero silent data loss (docs/EXCEL_DATA_FIDELITY_SPEC.md): every worksheet and every number in it is accounted for.
A number is "read" when some order value cites its cell as provenance (orders, lines, sizes, fabrics, yarns,
accessories all keep {sheet, cell}). Numbers nobody cites are grouped into row bands per sheet and reported with
the headings above them, so the reviewer sees, before approving, e.g. "Sheet PO rows 8–10, columns C–F (under
S, M, L, XL at row 7): 12 numbers not read". Text is counted but not listed: most unread text is labels.

Nothing here changes the orders; it only describes them against the cell model of the original file.
"""
from __future__ import annotations
import hashlib
import json
import re

TOTAL_WORDS = re.compile(r'^\s*(sub|grand|g|overall|net)?\s*(totals?|ttl|tot)\b', re.I)
HEADING_ROWS = 6      # a band's heading is the nearest text row at most this far above it
BAND_GAP = 1          # rows without unread numbers allowed inside one band
MAX_BANDS = 40        # bands listed per sheet (the count is always complete)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _letters(col):
    s = ''
    while col:
        col, r = divmod(col - 1, 26)
        s = chr(65 + r) + s
    return s


def referenced(value, out=None):
    """{sheet: {cell, ...}} of every {sheet, cell} provenance dict inside value (orders as stored in a draft)."""
    out = {} if out is None else out
    if isinstance(value, dict):
        if isinstance(value.get('sheet'), str) and isinstance(value.get('cell'), str):
            out.setdefault(value['sheet'], set()).add(value['cell'].upper())
        for v in value.values():
            if isinstance(v, (dict, list)):
                referenced(v, out)
    elif isinstance(value, list):
        for v in value:
            referenced(v, out)
    return out


def _heading(by_rc, rows, cols, first_row):
    """The text cells of the nearest row above first_row (within HEADING_ROWS) that has text in these columns."""
    for r in range(first_row - 1, max(0, first_row - 1 - HEADING_ROWS), -1):
        words = [str(by_rc[(r, c)]).strip() for c in cols if isinstance(by_rc.get((r, c)), str) and str(by_rc[(r, c)]).strip()]
        if words:
            return r, words
    return None, []


def _row_label(by_rc, row, before_col):
    """The first text cell of a row left of before_col ("Grand Total", a colour name), or ''."""
    for c in range(1, before_col):
        v = by_rc.get((row, c))
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _bands(cells, by_rc):
    """Group unread number cells into bands of nearby rows: [{first, last, cols, cells}]."""
    rows = {}
    for c in cells:
        rows.setdefault(c['row'], []).append(c)
    bands = []
    for r in sorted(rows):
        if bands and r - bands[-1]['last'] <= BAND_GAP + 1:
            bands[-1]['last'] = r; bands[-1]['cells'] += rows[r]
        else:
            bands.append(dict(first=r, last=r, cells=list(rows[r])))
    for b in bands:
        b['cols'] = sorted({c['col'] for c in b['cells']})
    return bands


def _describe(sheet, band, by_rc):
    cols = band['cols']; first, last = band['first'], band['last']
    head_row, words = _heading(by_rc, None, cols, first)
    labels = {_row_label(by_rc, c['row'], min(cols)) for c in band['cells']}
    totals = bool(words) and all(TOTAL_WORDS.match(w) for w in words) or (labels and all(TOTAL_WORDS.match(x) for x in labels if x) and all(labels))
    hidden = all(c.get('hidden') for c in band['cells'])
    where = f'row {first}' if first == last else f'rows {first}–{last}'
    span = _letters(cols[0]) if len(cols) == 1 else f'{_letters(cols[0])}–{_letters(cols[-1])}'
    n = len(band['cells'])
    text = f'Sheet “{sheet["name"]}” {where}, column{"" if len(cols) == 1 else "s"} {span}: {n} number{"" if n == 1 else "s"} not read'
    if words:
        shown = ', '.join(words[:8]) + (', …' if len(words) > 8 else '')
        text += f' (under {shown} at row {head_row})'
    if hidden:
        text += '; these cells are hidden in Excel'
    sample = [dict(cell=c['cell'], value=c['value']) for c in sorted(band['cells'], key=lambda c: (c['row'], c['col']))[:6]]
    return dict(rows=[first, last], columns=[_letters(c) for c in cols], count=n, heading=words[:12], heading_row=head_row,
                kind='totals' if totals else 'hidden' if hidden else 'data', text=text + '.', sample=sample)


def manifest(sheets, orders, notes=()):
    """The coverage of one draft. exclusions: sentences the approver must see (data that is not in any order)."""
    used = referenced(orders)
    out = []; exclusions = []
    totals = dict(worksheets=len(sheets), read=0, not_read=0, hidden=0, cells=0, numbers=0, numbers_read=0, numbers_not_read=0)
    for sheet in sheets:
        cells = [c for c in sheet.get('cells') or [] if c.get('value') not in (None, '')]
        mine = used.get(sheet['name'], set())
        numbers = [c for c in cells if is_number(c['value'])]
        unread = [c for c in numbers if c['cell'].upper() not in mine]
        read = bool(mine)
        status = 'read' if read else 'hidden, not read' if sheet.get('hidden') else 'not read' if cells else 'empty'
        by_rc = {(c['row'], c['col']): c['value'] for c in cells}
        bands = [_describe(sheet, b, by_rc) for b in _bands(unread, by_rc)]
        entry = dict(name=sheet['name'], hidden=bool(sheet.get('hidden')), status=status, cells=len(cells), numbers=len(numbers),
                     numbers_read=len(numbers) - len(unread), numbers_not_read=len(unread),
                     text_cells=len(cells) - len(numbers), cells_cited=len(mine), regions=bands[:MAX_BANDS],
                     regions_total=len(bands))
        out.append(entry)
        totals['cells'] += len(cells); totals['numbers'] += len(numbers)
        totals['numbers_read'] += entry['numbers_read']; totals['numbers_not_read'] += len(unread)
        totals['read' if read else 'hidden' if sheet.get('hidden') else 'not_read'] += 1 if cells or read else 0
        if not read and cells:
            what = f'{len(numbers)} number{"" if len(numbers) == 1 else "s"} and {len(cells) - len(numbers)} text cells'
            exclusions.append(f'Sheet “{sheet["name"]}”{" (hidden in Excel)" if sheet.get("hidden") else ""} was not read: '
                              f'{what} are not in any order.' if numbers else
                              f'Sheet “{sheet["name"]}”{" (hidden in Excel)" if sheet.get("hidden") else ""} was not read '
                              f'({len(cells)} text cells, no numbers).')
        elif read:
            exclusions += [b['text'] for b in bands if b['kind'] != 'totals']
    result = dict(sheets=out, totals=totals, exclusions=exclusions, notes=list(notes or ()))
    result['digest'] = hashlib.sha256(json.dumps(dict(sheets=out, exclusions=exclusions), sort_keys=True, default=str)
                                      .encode()).hexdigest()
    return result
