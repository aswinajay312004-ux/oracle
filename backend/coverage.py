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


def _close(a, b):
    return abs(a - b) <= 0.005 + 1e-9 * max(abs(a), abs(b))


def _run(by_rc, row, col, dr, dc):
    """The numbers next to (row, col) going up (dr=-1) or left (dc=-1) until the first cell that is not a number."""
    out = []; r, c = row + dr, col + dc
    while r > 0 and c > 0 and is_number(by_rc.get((r, c))):
        out.append((r, c)); r += dr; c += dc
    return out


def _is_total_label(by_rc, row, col):
    head = _heading(by_rc, None, [col], row)[1]
    label = _row_label(by_rc, row, col)
    return bool(head and TOTAL_WORDS.match(head[0])) or bool(label and TOTAL_WORDS.match(label))


def _checked_totals(unread, by_rc, known):
    """Unread numbers that are totals: the sum (of two or more) of the numbers right above them or right left of them,
    where those numbers were read (or are themselves checked totals) or the row/column is labelled Total. Such a
    number is checked against what was read, not lost. The read-backed or labelled condition keeps an ordinary size
    row (1, 2, 3) from being taken for a total by coincidence."""
    found = set(); changed = True
    while changed:
        changed = False
        for c in unread:
            rc = (c['row'], c['col'])
            if rc in found:
                continue
            for run in (_run(by_rc, *rc, -1, 0), _run(by_rc, *rc, 0, -1)):
                if len(run) < 2 or not _close(sum(by_rc[x] for x in run), c['value']):
                    continue
                if all(x in known or x in found for x in run) or _is_total_label(by_rc, *rc):
                    found.add(rc); changed = True
                    break
    return found


def _repeat_of(band, by_rc, accounted):
    """Rows a band repeats exactly (same numbers in the same columns, same row labels, a fixed number of rows higher),
    e.g. a SUMMARY table that copies the order table. Returns the source rows or None."""
    first = min(band['cells'], key=lambda c: (c['row'], c['col']))
    for dr in sorted({first['row'] - r for (r, col) in accounted if col == first['col'] and r < first['row']
                      and is_number(by_rc.get((r, col))) and _close(by_rc[(r, col)], first['value'])}):
        if all((c['row'] - dr, c['col']) in accounted and is_number(by_rc.get((c['row'] - dr, c['col'])))
               and _close(by_rc[(c['row'] - dr, c['col'])], c['value'])
               and _row_label(by_rc, c['row'], min(band['cols'])).upper() == _row_label(by_rc, c['row'] - dr, min(band['cols'])).upper()
               for c in band['cells']):
            return band['first'] - dr, band['last'] - dr
    return None


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
    totals = dict(worksheets=len(sheets), read=0, not_read=0, hidden=0, cells=0, numbers=0, numbers_read=0, numbers_checked=0,
                  numbers_not_read=0)
    for sheet in sheets:
        cells = [c for c in sheet.get('cells') or [] if c.get('value') not in (None, '')]
        mine = used.get(sheet['name'], set())
        numbers = [c for c in cells if is_number(c['value'])]
        unread = [c for c in numbers if c['cell'].upper() not in mine]
        read = bool(mine)
        status = 'read' if read else 'hidden, not read' if sheet.get('hidden') else 'not read' if cells else 'empty'
        by_rc = {(c['row'], c['col']): c['value'] for c in cells}
        # On a sheet that was read, totals of what was read, exact repeats of it (a SUMMARY table) and tables of zeros
        # are checked, not lost: they are listed under "checked", and only the rest is "not read".
        known = {(c['row'], c['col']) for c in numbers if c['cell'].upper() in mine}
        totals_found = _checked_totals(unread, by_rc, known) if read else set()
        accounted = known | totals_found
        bands = []
        for b in _bands([c for c in unread if (c['row'], c['col']) not in totals_found], by_rc):
            item = _describe(sheet, b, by_rc)
            source = _repeat_of(b, by_rc, accounted) if read else None
            if read and all(c['value'] == 0 for c in b['cells']):
                item.update(kind='zeros', text=item['text'].replace(' not read', ', all 0'))
            elif source:
                item.update(kind='repeat', text=item['text'].replace(' not read', f' repeat rows {source[0]}–{source[1]} exactly'
                                                                     if source[0] != source[1] else f' repeat row {source[0]} exactly'))
                accounted |= {(c['row'], c['col']) for c in b['cells']}
            if item['kind'] == 'totals':
                item['text'] = item['text'].replace(' not read', ' in a Total row')
            bands.append(item)
        checked_n = len(totals_found) + sum(b['count'] for b in bands if b['kind'] in ('zeros', 'repeat', 'totals'))
        not_read_n = len(unread) - checked_n
        checked = ([f'Sheet “{sheet["name"]}”: {len(totals_found)} total{"" if len(totals_found) == 1 else "s"} match the numbers they add up.']
                   if totals_found else []) + [b['text'] for b in bands if b['kind'] in ('zeros', 'repeat', 'totals')]
        entry = dict(name=sheet['name'], hidden=bool(sheet.get('hidden')), status=status, cells=len(cells), numbers=len(numbers),
                     numbers_read=len(numbers) - len(unread), numbers_checked=checked_n, numbers_not_read=not_read_n,
                     text_cells=len(cells) - len(numbers), cells_cited=len(mine), regions=bands[:MAX_BANDS],
                     regions_total=len(bands), checked=checked)
        out.append(entry)
        totals['cells'] += len(cells); totals['numbers'] += len(numbers)
        totals['numbers_read'] += entry['numbers_read']; totals['numbers_not_read'] += not_read_n
        totals['numbers_checked'] = totals.get('numbers_checked', 0) + checked_n
        totals['read' if read else 'hidden' if sheet.get('hidden') else 'not_read'] += 1 if cells or read else 0
        if not read and cells:
            what = f'{len(numbers)} number{"" if len(numbers) == 1 else "s"} and {len(cells) - len(numbers)} text cells'
            exclusions.append(f'Sheet “{sheet["name"]}”{" (hidden in Excel)" if sheet.get("hidden") else ""} was not read: '
                              f'{what} are not in any order.' if numbers else
                              f'Sheet “{sheet["name"]}”{" (hidden in Excel)" if sheet.get("hidden") else ""} was not read '
                              f'({len(cells)} text cells, no numbers).')
        elif read:
            exclusions += [b['text'] for b in bands if b['kind'] in ('data', 'hidden')]
    result = dict(sheets=out, totals=totals, exclusions=exclusions, checked=[t for e in out for t in e['checked']],
                  notes=list(notes or ()))
    result['digest'] = hashlib.sha256(json.dumps(dict(sheets=out, exclusions=exclusions), sort_keys=True, default=str)
                                      .encode()).hexdigest()
    return result
