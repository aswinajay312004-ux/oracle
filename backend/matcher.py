"""Deterministic, explainable field matcher and validator for unknown buyer layouts
(docs/EXCEL_UNDERSTANDING.md §3–4). Ideas from COMA/Valentine (name + instance matchers combined),
FlexMatcher and spreadsheet schema matching; no code copied.

Every score is explained (label words + what the value looks like) and every proposal — the matcher's
own, the local AI's, the heuristic's, a saved template's or a person's — goes through check(). Nothing
here saves anything: a person confirms the layout once.
"""
from __future__ import annotations
from copy import deepcopy
from functools import lru_cache
import re
from . import fields as F, workbooks as W

SIZE = re.compile(r'^(xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]xl|\d{1,3}\s*(-\s*\d{1,3})?\s*(m|mth|mths|month|months|y|yr|yrs|year|years)?'
                  r'|up to \d+\s*m\w*|first size|newborn|nb|one size|free size|\d+\s*lb|\d+(\.\d+)?\s*-\s*\d+(\.\d+)?\s*(y|yrs?|years?))$', re.I)
ABBREVIATIONS = {'qty': 'quantity', 'no': 'number', 'num': 'number', 'dt': 'date', 'col': 'colour', 'clr': 'colour',
                 'color': 'colour', 'ref': 'reference', 'desc': 'description', 'cust': 'customer', 'del': 'delivery',
                 'exf': 'ex factory'}
HIGH, MEDIUM = 0.85, 0.6
FLOOR = 0.5            # weaker name matches are not shown at all
MASTER_ROLES = {'buyer': 'buyer', 'color': 'color', 'size': 'size'}
LABEL_ROWS = 60


# --- scoring -----------------------------------------------------------------------------------
def expand(text):
    """Normalised words with buyer-file abbreviations spelled out ("Del Dt" → "delivery date")."""
    n = W.norm(str(text or '').replace('#', ' number '))
    n = re.sub(r'\bex fty\b', 'ex factory', n)
    return ' '.join(ABBREVIATIONS.get(t, t) for t in n.split())


def jaccard(a, b):
    a, b = set(a.split()), set(b.split())
    return len(a & b) / len(a | b) if a and b else 0.0


def dice(a, b):
    def grams(s):
        s = f' {s} '
        return {s[i:i + 3] for i in range(len(s) - 2)}
    x, y = grams(a), grams(b)
    return 2 * len(x & y) / (len(x) + len(y)) if a and b else 0.0


def name_score(label, aliases):
    """(score, alias): 1.0 for an exact alias after normalisation, else the best of token Jaccard and
    character-trigram Dice against every alias, after expanding abbreviations."""
    return _name_score(str(label or ''), tuple(aliases))


@lru_cache(maxsize=20000)
def _name_score(label, aliases):
    n = W.norm(label)
    if not n:
        return 0.0, None
    best = (0.0, None)
    for a in aliases:
        if W.norm(a) == n:
            return 1.0, a
        el, ea = expand(label), expand(a)
        s = max(jaccard(el, ea), dice(el, ea))
        if s > best[0]:
            best = (round(s, 2), a)
    return best


def masters_index(masters):
    """{'buyer'|'color'|'size': {normalised labels}} from Oracle master labels (empty when offline)."""
    out = {}
    for role, values in (masters or {}).items():
        out[role] = {W.norm(v.get('label') if isinstance(v, dict) else v) for v in values or []} - {''}
    return out


def is_number(v):
    return not isinstance(v, bool) and W.number(v) is not None


def is_date(v):
    return bool(v not in (None, '') and W.iso_date(v)[0])


def size_like(v, mi=None):
    t = str(v if v is not None else '').strip()
    return bool(t) and (bool(SIZE.match(t)) or W.norm(t) in (mi or {}).get('size', set()))


def show(v):
    return f'{v:g}' if isinstance(v, float) else str(v)


def value_score(kind, key, v, mi=None):
    """(score 0–1, evidence) for one value of a field kind; 0 means the value cannot be this field."""
    if v in (None, ''):
        return 0.0, 'the cell is empty'
    known = W.norm(v) in (mi or {}).get(MASTER_ROLES.get(key), set())
    role = {'color': 'colour'}.get(key, key)
    if kind == 'date':
        iso = W.iso_date(v)[0]
        return (1.0, f'“{show(v)}” reads as the date {iso}') if iso else (0.0, 'not a date')
    if kind in ('quantity', 'number'):
        return (1.0, f'{show(v)} is a number') if is_number(v) and W.number(v) >= 0 else (0.0, 'not a number')
    if kind == 'size':
        if known:
            return 1.0, f'“{show(v)}” is in the size master'
        return (0.9, f'“{show(v)}” looks like a size') if size_like(v) else (0.0, 'not a size')
    if kind == 'unit':
        u, _ = W.unit_words(v)
        return (1.0, f'“{show(v)}” names a unit') if u in ('PCS', 'PACK') else (0.0, 'not pieces or packs')
    if kind == 'code':
        t = str(v).strip()
        if isinstance(v, bool) or is_date(v):
            return 0.0, 'not a code'
        if len(t) <= 30 and t.count(' ') <= 3 and re.search(r'[A-Za-z0-9]', t):
            return 1.0, f'“{t}” looks like a code'
        return 0.3, f'“{t[:40]}” is long for a code'
    # text and colour: words, not numbers or dates
    if is_number(v) or is_date(v):
        return 0.0, 'a number or date, not text'
    if known:
        return 1.0, f'value found in the {role} master'
    return 0.8, f'“{show(v)}” is text'


def confidence(score):
    return 'high' if score >= HIGH else 'medium' if score >= MEDIUM else 'low'


def label_for(g, r, c):
    """(row, col) of the label of a value cell: nearest text to the left (≤3 cells), else above (≤2)."""
    for cc in range(c - 1, max(0, c - 4), -1):
        v = g.v(r, cc)
        if v not in (None, ''):
            return (r, cc) if isinstance(v, str) and not is_number(v) and not is_date(v) else None
    for rr in range(r - 1, max(0, r - 3), -1):
        v = g.v(rr, c)
        if v not in (None, ''):
            return (rr, c) if isinstance(v, str) and not is_number(v) and not is_date(v) else None
    return None


def evaluate(g, key, r, c, mi=None, label_at=None):
    """The matcher's view of one (field, value cell): score, confidence and evidence."""
    Fv = g.fields; label_at = label_at or label_for(g, r, c)
    label = W.text(g.v(*label_at)) if label_at else None
    ns, alias = name_score(label, Fv.aliases(key)) if label else (0.0, None)
    vs, vev = value_score(Fv.kind(key), key, g.v(r, c), mi)
    score = round(0.6 * ns + 0.4 * vs, 2)
    evidence = []
    if label and ns == 1.0:
        evidence.append(f'label “{label}” = alias of {Fv.label(key)}')
    elif label and alias:
        evidence.append(f'label “{label}” is like “{alias}” ({ns:.2f})')
    elif label:
        evidence.append(f'label “{label}” does not name {Fv.label(key)}')
    evidence.append(vev)
    return dict(cell=W.address(c, r), label_cell=W.address(label_at[1], label_at[0]) if label_at else None, label=label,
                value=g.v(r, c), score=score, name_score=ns, value_score=vs, confidence=confidence(score), source='matcher',
                evidence=evidence)


# --- tables ------------------------------------------------------------------------------------
def _body(g, header, cols, stop_col=None):
    """Data rows under a header: until a TOTAL row or the first row with nothing in these columns."""
    rows = []
    for r in range(header + 1, min(header + 201, g.rows + 1)):
        if stop_col and W.norm(g.v(r, stop_col)) in ('total', 'grand total'):
            break
        if any(isinstance(g.v(r, c), str) and W.norm(g.v(r, c)) in ('total', 'grand total') for c in range(1, min(g.cols, 3) + 1)):
            break
        if all(g.v(r, c) in (None, '') for c in cols):
            if rows:
                break
            continue
        rows.append(r)
    return rows


def _numbers(g, rows, cols):
    filled = [(r, c, g.v(r, c)) for r in rows for c in cols if g.v(r, c) not in (None, '')]
    numeric = [x for x in filled if is_number(x[2])]
    negative = next((x for x in numeric if W.number(x[2]) < 0), None)
    return filled, numeric, negative


def matrix_at(g, r, mi):
    """A size matrix headed by row r: ≥3 size headings across the row with numbers below them."""
    filled = [(c, g.v(r, c)) for c in range(1, g.cols + 1) if g.v(r, c) not in (None, '')]
    if len(filled) < 3:
        return None
    runs, run = [], []
    for c, v in filled:
        if size_like(v, mi) and not isinstance(v, bool):
            run.append((c, v))
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)
    runs = [x for x in runs if len(x) >= 3]
    if not runs:
        return None
    run = max(runs, key=lambda x: (sum(isinstance(v, str) for _, v in x), len(x)))
    first, last = run[0][0], run[-1][0]
    texts = sum(isinstance(v, str) for _, v in run)
    left = [(c, v) for c, v in filled if c < first and isinstance(v, str)]
    color = next((c for c, v in left if name_score(v, g.fields.aliases('color'))[0] >= HIGH), None)
    names = g.fields.aliases('style') + g.fields.aliases('description') + ['garment', 'garment style']
    label = next((c for c, v in left if c != color and name_score(v, names)[0] >= HIGH), None)
    total = next((c for c, v in filled if c > last and isinstance(v, str) and
                  (W.norm(v).startswith('total') or W.strip_units(W.norm(v)) in {W.norm(a) for a in g.fields.aliases('quantity')})), None)
    # ≥ 3 text headings, or numeric sizes (28, 30, 32) named as a table by a colour/style/total heading —
    # otherwise this is a row of quantities, not headings.
    if texts < 3 and not (color or label or total) or sum(isinstance(v, str) for _, v in filled) < 2:
        return None
    rows = _body(g, r, range(first, last + 1), label or color)
    cells, numeric, negative = _numbers(g, rows, range(first, last + 1))
    if not numeric or len(numeric) < 0.8 * len(cells) or negative:
        return None
    sizes = [W.text(v) for _, v in run]
    evidence = [f'{len(sizes)} size headings in row {r}: {", ".join(sizes[:8])}{"…" if len(sizes) > 8 else ""}',
                f'row{"s" if len(rows) > 1 else ""} {rows[0]}{"–" + str(rows[-1]) if len(rows) > 1 else ""} hold numbers under the sizes']
    if color:
        evidence.append(f'“{W.text(g.v(r, color))}” heads the colour column')
    if total:
        evidence.append(f'“{W.text(g.v(r, total))}” heads the total column')
    conf = 'high' if texts >= 3 else 'medium'
    return dict(definition=dict(type='matrix', header_row=r, label_col=label, color_col=color, first_size_col=first,
                                last_size_col=last, total_col=total),
                info=dict(type='matrix', header_row=r, confidence=conf, evidence=evidence, source='matcher'),
                rows=(r, rows[-1]), score=(2 if conf == 'high' else 1) + 0.01 * len(sizes))


def flat_at(g, r):
    """A one-row-per-size table headed by row r: size and quantity headings, numbers under the quantity."""
    Fv = g.fields; cols = {}; evidence = []
    heads = [(c, g.v(r, c)) for c in range(1, g.cols + 1) if isinstance(g.v(r, c), str) and not is_number(g.v(r, c))]
    if len(heads) < 2:
        return None
    scored = []
    for c, v in heads:
        for key in Fv.column_keys:
            ns, alias = name_score(v, Fv.aliases(key))
            if key == 'quantity' and ns < 1.0:
                stripped = W.strip_units(W.norm(v))
                if stripped != W.norm(v) and stripped in {W.norm(a) for a in Fv.aliases('quantity')}:
                    ns, alias = 1.0, stripped
            if ns >= HIGH:
                scored.append((ns, -c, key, c, v, alias))
    for ns, _, key, c, v, alias in sorted(scored, reverse=True):
        if key in cols or c in cols.values():
            continue
        cols[key] = c
        evidence.append(f'“{W.text(v)}” heads the {Fv.label(key).lower()} column' + ('' if ns == 1.0 else f' (like “{alias}”, {ns:.2f})'))
    if not {'size', 'quantity'} <= cols.keys():
        return None
    rows = _body(g, r, list(cols.values()), cols.get('style'))
    cells, numeric, negative = _numbers(g, rows, [cols['quantity']])
    if not numeric or len(numeric) < 0.8 * len(cells) or negative:
        return None
    evidence.append(f'{len(numeric)} of {len(cells)} rows under “{W.text(g.v(r, cols["quantity"]))}” hold numbers')
    conf = 'high' if all(x[0] == 1.0 for x in scored if x[2] in ('size', 'quantity')) else 'medium'
    return dict(definition=dict(type='flat', header_row=r, columns=cols),
                info=dict(type='flat', header_row=r, confidence=conf, evidence=evidence, source='matcher'),
                rows=(r, rows[-1]), score=(2 if conf == 'high' else 1) + 0.01 * len(cols))


def find_table(g, mi=None):
    best = None
    for r in range(1, min(g.rows, 120) + 1):
        for cand in (matrix_at(g, r, mi), flat_at(g, r)):
            if cand and (best is None or cand['score'] > best['score']):
                best = cand
    return best


# --- header fields -----------------------------------------------------------------------------
def header_proposals(g, mi=None, skip_rows=()):
    """{field: proposal} with one-to-one assignment (best scores first; a cell is used once), plus the
    label-confirmed fields whose value cannot be that field (rejected with the reason)."""
    Fv = g.fields; cands = []; rejected = []
    for cell in sorted(g.sheet['cells'], key=lambda x: (x['row'], x['col'])):
        r, c, v = cell['row'], cell['col'], cell['value']
        if r > LABEL_ROWS or r in skip_rows or not isinstance(v, str) or is_number(v) or is_date(v):
            continue
        n = W.norm(v)
        if not 2 <= len(n) <= 40 or sum(ch.isdigit() for ch in n) > len(n) // 3:
            continue
        targets = [t for t in (g.right(r, c), g.below(r, c)) if t and t[0] not in skip_rows]
        targets = [t for t in targets if not W.looks_like_label(g.v(*t), Fv)]
        for key in Fv.header_keys:
            ns, _ = name_score(v, Fv.aliases(key))
            if ns < FLOOR:
                continue
            for t in targets:
                p = evaluate(g, key, *t, mi, label_at=(r, c))
                if p['value_score'] == 0:
                    if ns >= HIGH:
                        rejected.append(dict(field=key, cell=p['cell'], value=p['value'], source='matcher',
                                             reason=f'{p["cell"]} holds “{show(p["value"])}”, which is {_not(Fv.kind(key))}'))
                    continue
                cands.append((p['score'], -r, -c, key, p))
    chosen = {}; used = set()
    for score, _, _, key, p in sorted(cands, key=lambda x: (-x[0], -x[1], -x[2])):
        if key in chosen or p['cell'] in used or p['label_cell'] in used:
            continue
        chosen[key] = p; used |= {p['cell'], p['label_cell']}
    rejected = [x for x in rejected if (chosen.get(x['field']) or {}).get('cell') != x['cell']]
    return chosen, list({(x['field'], x['cell']): x for x in rejected}.values())


def _not(kind):
    return {'date': 'not a date', 'quantity': 'not a number', 'number': 'not a number', 'size': 'not a size',
            'unit': 'not pieces or packs', 'code': 'not a code'}.get(kind, 'a number or date, not text')


# --- validator (acceptance test D) ---------------------------------------------------------------
def letter(col):
    return W.address(col, 1)[:-1] if isinstance(col, int) and col >= 1 else str(col)


def check(sheet, definition, fields=None, masters=None, source=None):
    """(ok_definition, rejected[]). Each wrong field is dropped with a reason; the rest is kept."""
    Fv = F.resolve(fields); g = W.Grid(sheet, Fv); mi = masters_index(masters)
    d = deepcopy(definition or {}); rejected = []; ok_fields = {}; used = {}
    end = W.address(max(g.cols, 1), max(g.rows, 1))

    def reject(field, cell, value, reason):
        rejected.append(dict(field=field, cell=cell, value=value, reason=reason, **({'source': source} if source else {})))

    for key, ref in (d.get('fields') or {}).items():
        if key not in Fv.header_keys:
            reject(key, ref, None, f'“{key}” is not an order field'); continue
        try:
            r, c = W.cell_ref(ref)
        except ValueError:
            reject(key, ref, None, f'“{ref}” is not a cell address such as C4'); continue
        cell = W.address(c, r); v = g.v(r, c)
        if r > g.rows or c > g.cols:
            reject(key, cell, None, f'{cell} is outside the sheet (it ends at {end})'); continue
        if v in (None, ''):
            reject(key, cell, None, f'{cell} is empty'); continue
        if cell in used:
            reject(key, cell, v, f'{cell} is already used for {Fv.label(used[cell])}'); continue
        if isinstance(v, str) and (W.norm(v) in Fv.label_words or W.norm(v) in W.OTHER_LABELS):
            reject(key, cell, v, f'{cell} holds “{v}”, which is a field label, not a value'); continue
        kind = Fv.kind(key)
        if kind == 'date' and not is_date(v):
            reject(key, cell, v, f'{cell} holds “{show(v)}”, which is not a date'); continue
        if kind == 'number' and not is_number(v):
            reject(key, cell, v, f'{cell} holds “{show(v)}”, which is not a number'); continue
        if key in ('buyer', 'description') and (is_number(v) or is_date(v)):
            reject(key, cell, v, f'{cell} holds {show(v)}, a {"date" if is_date(v) else "number"} — not a {Fv.label(key).lower()}'); continue
        used[cell] = key; ok_fields[key] = cell
    d['fields'] = ok_fields
    t = d.get('table')
    d['table'] = _check_table(g, t, Fv, mi, reject) if t else None
    return d, rejected


def _check_table(g, t, Fv, mi, reject):
    hr = t.get('header_row')
    if hr in (None, ''):
        reject('table', None, None, 'choose the header row of the table')
        return None
    if not isinstance(hr, int) or hr < 1 or hr > g.rows:
        reject('table', None, hr, f'the header row {hr} is outside the sheet (it has {g.rows} rows)')
        return None
    if t.get('type') == 'flat':
        cols = {}
        for key, col in (t.get('columns') or {}).items():
            if key not in Fv.column_keys:
                reject(key, None, col, f'“{key}” cannot be a table column'); continue
            if not isinstance(col, int) or col < 1 or col > g.cols:
                reject(key, None, col, f'column {letter(col)} is outside the sheet (the last column is {letter(g.cols)})'); continue
            if col in cols.values():
                other = next(k for k, c in cols.items() if c == col)
                reject(key, W.address(col, hr), g.v(hr, col), f'column {letter(col)} is already used for {Fv.label(other)}'); continue
            cols[key] = col
        if 'quantity' in cols:
            q = cols['quantity']; head = W.address(q, hr); name = W.text(g.v(hr, q))
            rows = _body(g, hr, list(cols.values()), cols.get('style'))
            filled, numeric, negative = _numbers(g, rows, [q])
            problem = None
            if negative:
                problem = f'{W.address(negative[1], negative[0])} holds {show(negative[2])}, a negative quantity'
            elif not filled:
                problem = f'column {letter(q)} (“{name}”) has no quantities under its heading'
            elif len(numeric) < 0.8 * len(filled):
                problem = f'column {letter(q)} (“{name}”) is not numbers: {len(numeric)} of {len(filled)} filled rows hold a quantity'
            if problem:
                reject('quantity', head, g.v(hr, q), problem); del cols['quantity']
        return dict(t, columns=cols)
    if t.get('type') == 'matrix':
        first, last = t.get('first_size_col'), t.get('last_size_col')
        if not all(isinstance(x, int) and 1 <= x <= g.cols for x in (first, last)) or last < first:
            reject('size', None, None, f'the size columns {letter(first)}–{letter(last)} are not inside the sheet (the last column is {letter(g.cols)})')
            return None
        span = f'{W.address(first, hr)}:{W.address(last, hr)}'
        heads = [g.v(hr, c) for c in range(first, last + 1) if g.v(hr, c) not in (None, '')]
        sizey = sum(size_like(v, mi) for v in heads)
        if not heads or sizey < 0.6 * len(heads):
            reject('size', span, ', '.join(W.text(v) for v in heads[:6]) or None,
                   f'row {hr} columns {letter(first)}–{letter(last)} do not look like sizes ({sizey} of {len(heads)} headings are sizes)')
            return None
        out = dict(t)
        for k in ('label_col', 'color_col', 'total_col'):
            col = t.get(k)
            if col in (None, '', 0):
                continue
            if not isinstance(col, int) or col < 1 or col > g.cols:
                reject({'label_col': 'style', 'color_col': 'color', 'total_col': 'quantity'}[k], None, col,
                       f'column {letter(col)} is outside the sheet (the last column is {letter(g.cols)})')
                out[k] = None
        rows = _body(g, hr, range(first, last + 1), out.get('label_col') or out.get('color_col'))
        filled, numeric, negative = _numbers(g, rows, range(first, last + 1))
        problem = None
        if negative:
            problem = f'{W.address(negative[1], negative[0])} holds {show(negative[2])}, a negative quantity'
        elif not filled:
            problem = f'there are no quantities under the sizes in {span}'
        elif len(numeric) < 0.8 * len(filled):
            problem = f'the cells under the sizes in {span} are not numbers: {len(numeric)} of {len(filled)} hold a quantity'
        if problem:
            reject('quantity', span, None, problem)
            return None
        return out
    reject('table', None, t.get('type'), 'choose a table type: one row per size, or sizes across columns')
    return None


# --- proposals ---------------------------------------------------------------------------------
def anchors(sheet, definition, fields=None):
    """anchors[field] = {cell, label_cell, label} for every header field, plus the table headings the
    definition relies on (table.<column>); used to refuse a template when a label has changed."""
    g = W.Grid(sheet, fields); d = definition or {}; out = {}
    for key, ref in (d.get('fields') or {}).items():
        r, c = W.cell_ref(ref); at = label_for(g, r, c)
        out[key] = dict(cell=W.address(c, r), label_cell=W.address(at[1], at[0]) if at else None,
                        label=W.text(g.v(*at)) if at else None)
    t = d.get('table') or {}; hr = t.get('header_row')
    cols = (t.get('columns') or {}) if t.get('type') == 'flat' else {k: t.get(k) for k in ('label_col', 'color_col', 'total_col')}
    for key, col in cols.items():
        if isinstance(hr, int) and isinstance(col, int) and isinstance(g.v(hr, col), str):
            out[f'table.{key}'] = dict(cell=W.address(col, hr), label_cell=W.address(col, hr), label=W.text(g.v(hr, col)))
    return out


def table_rows(found):
    return set(range(found['rows'][0], found['rows'][1] + 1)) if found else set()


def unit_info(g, table):
    if not table:
        return dict(value='', pieces_per_unit=None, evidence='no size table was found yet', cell=None)
    u = W.unit_evidence(g, table)
    return dict(value=u['qty_unit'], pieces_per_unit=u['pieces_per_unit'], evidence=u['provenance'].get('evidence'),
                cell=u['provenance'].get('cell'))


def explain(fields, table, sheet):
    shown = [k for k, p in fields.items() if p['confidence'] != 'low']
    what = {'matrix': 'a size table with sizes across columns', 'flat': 'a size table with one row per size'}.get((table or {}).get('type'))
    parts = [f'{len(shown)} order detail{"s" if len(shown) != 1 else ""}' if shown else '', what or '']
    found = ' and '.join(p for p in parts if p) or 'nothing certain'
    return f'Found {found} on sheet “{sheet}” from their labels and values; check each highlighted cell before applying.'


def suggest(sheet, fields=None, masters=None):
    """draft.suggestion for an unknown layout (spec §3 output shape), or None when nothing was found."""
    Fv = F.resolve(fields); g = W.Grid(sheet, Fv); mi = masters_index(masters)
    found = find_table(g, mi)
    props, rejected = header_proposals(g, mi, table_rows(found))
    definition = dict(fields={k: p['cell'] for k, p in props.items() if p['confidence'] != 'low'},
                      table=found['definition'] if found else None)
    ok, bad = check(sheet, definition, Fv, masters, source='matcher')
    for b in bad:
        if (props.get(b['field']) or {}).get('cell') == b['cell']:
            props.pop(b['field'])
        rejected.append(b)
    table = found['info'] if found and ok['table'] else None
    if table and ok['table']['type'] == 'flat' and 'style' not in ok['table']['columns'] and 'style' not in ok['fields']:
        table['evidence'] = table['evidence'] + ['no style column or style label was found — pick the style cell']
    if not props and not table:
        return None
    return dict(sheet=sheet['name'], definition=ok, fields=props, table=table, rejected=rejected,
                unit=unit_info(g, ok['table']), explanation=explain(props, ok['table'], sheet['name']), source='matcher')


def merge(sheet, base, proposal, fields=None, masters=None, source='ai'):
    """Cross-check another proposal (local AI or heuristic) against the matcher's: every field is
    validated, scored by the matcher and kept only where it is at least as good; agreement is 'both'."""
    Fv = F.resolve(fields); g = W.Grid(sheet, Fv); mi = masters_index(masters)
    out = deepcopy(base)
    given = proposal.get('fields') if isinstance(proposal.get('fields'), dict) else {}
    raw = dict(fields={k: v for k, v in given.items() if isinstance(k, str) and isinstance(v, str)},
               table=proposal.get('table') if isinstance(proposal.get('table'), dict) else None)
    ok, bad = check(sheet, raw, Fv, masters, source=source)
    out['rejected'] = out.get('rejected', []) + bad
    chosen = out['definition'].setdefault('fields', {})
    for key, ref in ok['fields'].items():
        r, c = W.cell_ref(ref); cell = W.address(c, r); mine = out['fields'].get(key)
        if mine and mine['cell'] == cell:
            if source == 'ai':
                mine['source'] = 'both' if mine['source'] == 'matcher' else mine['source']
            mine['evidence'].append('the local AI proposed the same cell' if source == 'ai' else 'the layout analysis agrees')
            if mine['confidence'] == 'low':
                mine['confidence'] = 'medium'
            chosen[key] = cell
            continue
        holder = next((k for k, v in chosen.items() if k != key and W.cell_ref(v) == (r, c)), None)
        if holder:
            out['rejected'].append(dict(field=key, cell=cell, value=g.v(r, c), source=source,
                                        reason=f'{cell} is already used for {Fv.label(holder)}'))
            continue
        p = evaluate(g, key, r, c, mi); p['source'] = source
        p['evidence'].insert(0, 'proposed by the local AI' if source == 'ai' else 'proposed by the layout analysis')
        if mine is None or p['score'] > mine['score']:
            out['fields'][key] = p
            if p['confidence'] != 'low':
                chosen[key] = cell
            else:
                chosen.pop(key, None)
        else:
            mine['evidence'].append(f'the {"local AI" if source == "ai" else "layout analysis"} proposed {cell} instead')
    theirs = ok.get('table')
    try:
        complete = bool(theirs) and W.validate_definition(sheet, dict(fields=chosen, table=theirs), Fv) is not None
    except ValueError:
        complete = False
    if complete:
        if not out['definition'].get('table'):
            out['definition']['table'] = theirs
            out['table'] = dict(type=theirs['type'], header_row=theirs['header_row'], confidence='medium', source=source,
                                evidence=['proposed by the local AI' if source == 'ai' else 'proposed by the layout analysis',
                                          'the validator accepts it on this sheet'])
            out['unit'] = unit_info(g, theirs)
        elif _same_table(out['definition']['table'], theirs):
            out['table']['source'] = 'both' if source == 'ai' else out['table']['source']
            out['table']['evidence'].append('the local AI proposed the same table' if source == 'ai' else 'the layout analysis agrees')
        else:
            out['table']['evidence'].append(f'the {"local AI" if source == "ai" else "layout analysis"} proposed a different table '
                                            f'(row {theirs.get("header_row")}); the matcher’s is kept')
    return out


def _same_table(a, b):
    if not a or not b or a.get('type') != b.get('type') or a.get('header_row') != b.get('header_row'):
        return False
    if a['type'] == 'flat':
        return all((a.get('columns') or {}).get(k) == (b.get('columns') or {}).get(k) for k in ('size', 'quantity'))
    return (a.get('first_size_col'), a.get('last_size_col')) == (b.get('first_size_col'), b.get('last_size_col'))


def empty(sheet):
    return dict(sheet=sheet['name'], definition=dict(fields={}, table=None), fields={}, table=None, rejected=[],
                unit=dict(value='', pieces_per_unit=None, evidence='no size table was found yet', cell=None),
                explanation='', source='matcher')
