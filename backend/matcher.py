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

# Sizes: letters (S, XL, XXXXL, 2XS), US plus sizes (1X–3X), toddler (2T–5T), months and years, numbers, cm.
SIZE = re.compile(r'^(x{0,3}s|m|x{0,4}l|[2-6]\s*xl|[2-5]\s*xs|\d\s*x|\d{1,2}\s*t|\d{1,3}\s*(-\s*\d{1,3})?\s*(m|mth|mths|month|months|y|yr|yrs|year|years)?'
                  r'|up to \d+\s*m\w*|first size|newborn|nb|one size|free size|\d+\s*lb|\d+(\.\d+)?\s*-\s*\d+(\.\d+)?\s*(y|yrs?|years?)'
                  r'|\d{1,2}\s*/\s*\d{1,2}\s*(y|yrs?|years?|m|mths?|months?)?|(xxs|xs|s|m|l|xl|xxl)\s*/\s*(xs|s|m|l|xl|xxl|xxxl)|\d{2,3}\s*cm)$', re.I)
# Garment-file abbreviations and the usual shortenings of them ("Shp Dt", "Colr", "Ord Date", "Sz").
ABBREVIATIONS = {'qty': 'quantity', 'qnty': 'quantity', 'qnt': 'quantity', 'no': 'number', 'num': 'number', 'dt': 'date',
                 'col': 'colour', 'clr': 'colour', 'colr': 'colour', 'color': 'colour', 'ref': 'reference', 'desc': 'description',
                 'cust': 'customer', 'byr': 'buyer', 'del': 'delivery', 'dlv': 'delivery', 'dlvy': 'delivery', 'exf': 'ex factory',
                 'xf': 'ex factory', 'shp': 'ship', 'ord': 'order', 'sz': 'size', 'sty': 'style', 'shd': 'shade'}
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
    if W.is_error(v):
        return 0.0, f'“{v}” is an Excel error (a formula that failed in the file)'
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
    """Data rows under a header: until a grand total or the first row with nothing in these columns. Other total
    rows (a "Total" under each colour block, sub totals) are skipped — the table goes on below them — and hidden
    rows are not read."""
    rows = []; cols = list(cols); span = range(1, max(cols + [3, stop_col or 0]) + 1); after_total = False
    heads = {c: W.norm(g.v(header, c)) for c in cols if isinstance(g.v(header, c), str)}
    for r in range(header + 1, min(header + 201, g.rows + 1)):
        if r in g.hidden_rows:
            continue
        kinds = {W.total_kind(g.v(r, c)) for c in span if isinstance(g.v(r, c), str)}
        if kinds & {'sub', 'total', 'grand'}:
            if 'grand' in kinds:
                break
            after_total = True; continue
        if after_total and not any(W.qty_value(g.v(r, c))[0] is not None for c in cols):
            break                                      # text after a total row: another table
        after_total = False
        if sum(1 for c, h in heads.items() if h and W.norm(g.v(r, c)) == h) >= 2:
            break                                      # the headings again: a second table starts here
        if all(g.v(r, c) in (None, '') for c in cols):
            if rows:
                break
            continue
        rows.append(r)
    return rows


def _numbers(g, rows, cols):
    """Filled cells, the ones holding a quantity (numbers, "1,500", "1,500 pcs") and the first negative one."""
    filled = [(r, c, g.v(r, c)) for r in rows for c in cols if g.v(r, c) not in (None, '')]
    numeric = [(r, c, W.qty_value(v)[0]) for r, c, v in filled if W.qty_value(v)[0] is not None]
    negative = next((x for x in numeric if x[2] < 0), None)
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
    # Headings of a two-row header are often merged over both rows ("Colour" in A4:A5 above sizes in row 5).
    left = [(c, g.head(r, c)) for c in range(1, first) if isinstance(g.head(r, c), str)]
    color = next((c for c, v in left if name_score(v, g.fields.aliases('color'))[0] >= HIGH), None)
    names = g.fields.aliases('style') + g.fields.aliases('description') + ['garment', 'garment style']
    label = next((c for c, v in left if c != color and name_score(v, names)[0] >= HIGH), None)
    like = None                       # a heading only like "colour" ("Fabric Colour", "Body Colour"): proposed, not certain
    if color is None:
        like = next((c for c, v in left if c != label and name_score(v, g.fields.aliases('color'))[0] >= MEDIUM), None)
        color = like
    right = [(c, g.head(r, c)) for c in range(last + 1, g.cols + 1) if isinstance(g.head(r, c), str)]
    total = next((c for c, v in right if W.total_heading(v, g.fields)), None)
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
        evidence.append(f'“{W.text(g.head(r, color))}” heads the colour column' + (' (like “colour”; check it)' if like else ''))
    if total:
        evidence.append(f'“{W.text(g.head(r, total))}” heads the total column')
    conf = 'high' if texts >= 3 and not like else 'medium'
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
    others = {}
    for ns, _, key, c, v, alias in sorted(scored, reverse=True):
        if c in cols.values():
            continue
        if key in cols:
            if ns == 1.0:
                others.setdefault(key, []).append((c, v))      # a second column with the same heading meaning
            continue
        cols[key] = c
        evidence.append(f'“{W.text(v)}” heads the {Fv.label(key).lower()} column' + ('' if ns == 1.0 else f' (like “{alias}”, {ns:.2f})'))
    if not {'size', 'quantity'} <= cols.keys():
        return None
    for key, alts in others.items():
        evidence.append(f'{" and ".join(f"column {letter(c)} (“{W.text(v)}”)" for c, v in alts)} also name{"s" if len(alts) == 1 else ""} '
                        f'the {Fv.label(key).lower()} — column {letter(cols[key])} is proposed; check which one is meant')
    rows = _body(g, r, list(cols.values()), cols.get('style'))
    cells, numeric, negative = _numbers(g, rows, [cols['quantity']])
    if not numeric or len(numeric) < 0.8 * len(cells) or negative:
        return None
    evidence.append(f'{len(numeric)} of {len(cells)} rows under “{W.text(g.v(r, cols["quantity"]))}” hold numbers')
    conf = 'high' if all(x[0] == 1.0 for x in scored if x[2] in ('size', 'quantity')) and not others else 'medium'
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
    for cell in sorted(g.cells.values(), key=lambda x: (x['row'], x['col'])):       # hidden cells are never proposed
        r, c, v = cell['row'], cell['col'], cell['value']
        if r > LABEL_ROWS or r in skip_rows or not isinstance(v, str) or is_number(v) or is_date(v):
            continue
        n = W.norm(v)
        if not 2 <= len(n) <= 40 or sum(ch.isdigit() for ch in n) > len(n) // 3:
            continue
        if g.strip(r):                       # a strip of labels with their values underneath: the value is below
            targets = [t for t in [W.label_value(g, r, c)] if t and t[0] not in skip_rows]
        else:
            targets = [t for t in (g.right(r, c), g.below(r, c)) if t and t[0] not in skip_rows]
        targets = [t for t in targets if not W.looks_like_label(g.v(*t), Fv)]
        caption = _caption_under_value(g, r, c, targets, skip_rows)
        if caption:
            targets = [t for t in targets if t != caption[1]] + [caption[0]]
        owner = Fv.owner(v)                  # a label that is another field's own alias ("PO No") is never this field's
        for key in Fv.header_keys:
            ns, _ = name_score(v, Fv.aliases(key))
            if ns < FLOOR or (owner and owner != key):
                continue
            for t in targets:
                p = evaluate(g, key, *t, mi, label_at=(r, c))
                if caption and t == caption[0] and p['value_score']:
                    # captions under their values: the value may be above or below — shown, never pre-selected
                    p['score'] = min(p['score'], 0.59); p['confidence'] = 'low'
                    p['evidence'].append(f'“{W.text(v)}” sits under “{show(g.v(*t))}” and above “{show(g.v(*caption[1]))}”: '
                                         'labels here may be written under their values — pick the cell')
                if p['value_score'] == 0:
                    if ns >= HIGH:
                        rejected.append(dict(field=key, cell=p['cell'], value=p['value'], source='matcher', label_cell=p['label_cell'],
                                             reason=f'{p["cell"]} holds “{show(p["value"])}”, which is {_not(Fv.kind(key))}'))
                    continue
                cands.append((p['score'], -r, -c, key, p))
    chosen = {}; used = set()
    for score, _, _, key, p in sorted(cands, key=lambda x: (-x[0], -x[1], -x[2])):
        if key in chosen or p['cell'] in used or p['label_cell'] in used:
            continue
        chosen[key] = p; used |= {p['cell'], p['label_cell']}
    # Two labels that both name a field exactly ("Item" and "Style No") with different values: shown, never pre-selected.
    for key, p in chosen.items():
        others = [x for _, _, _, k, x in cands if k == key and x['name_score'] == 1.0 and x['label_cell'] != p['label_cell']
                  and x['cell'] != p['cell'] and W.canon(show(x['value'])) != W.canon(show(p['value']))]
        master = (mi or {}).get(MASTER_ROLES.get(key)) or set()
        if others and p['name_score'] == 1.0:
            x = others[0]
            if W.norm(p['value']) in master and not any(W.norm(y['value']) in master for y in others):
                p['evidence'].append(f'{x["label_cell"]} “{x["label"]}” also names {Fv.label(key)}, but “{show(x["value"])}” is not in the '
                                     f'{MASTER_ROLES[key].replace("color", "colour")} master')
                continue                     # the Oracle master decides between the two
            p['score'] = min(p['score'], 0.59); p['confidence'] = 'low'
            p['evidence'].append(f'{x["label_cell"]} “{x["label"]}” also names {Fv.label(key)} and says “{show(x["value"])}” — pick the cell that is meant')
    # A label whose value was found (to its right) is not also reported for the other cell next to it (below).
    rejected = [x for x in rejected if (chosen.get(x['field']) or {}).get('cell') != x['cell']
                and (chosen.get(x['field']) or {}).get('label_cell') != x['label_cell']]
    return chosen, list({(x['field'], x['cell']): {k: v for k, v in x.items() if k != 'label_cell'} for x in rejected}.values())


def _caption_under_value(g, r, c, targets, skip_rows=()):
    """((above), (below)) when the label at (r, c) is a caption written under its value: a value directly above,
    no value to the right, and the cell below it sits right above the next caption (value / caption / value /
    caption stacks). Then the cell below belongs to the next caption, not to this one."""
    above, below = (r - 1, c), (r + 1, c)
    if r < 2 or below not in targets or (r - 1) in skip_rows or g.right(r, c) in targets:
        return None
    up = g.v(*above)
    if up in (None, '') or W.looks_like_label(up, g.fields):
        return None
    return (above, below) if W.looks_like_label(g.v(r + 2, c), g.fields) else None


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
            reject(key, cell, None, f'{cell} is in a hidden row or column of the file' if (r, c) in g.hidden else f'{cell} is empty'); continue
        if cell in used:
            reject(key, cell, v, f'{cell} is already used for {Fv.label(used[cell])}'); continue
        if g.err(r, c):
            reject(key, cell, v, f'{cell} holds the Excel error “{v}” (a formula that failed in the file), not a value'); continue
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


def _sample(values, n=3):
    return ', '.join(f'“{show(v)}”' for v in list(dict.fromkeys(values))[:n])


def _not_sizes(g, hr, cols, mi):
    """Why the flat size column cannot be the sizes, or None. Sizes are written many ways, so only a clear sign
    refuses it: the colour (or style) column holds the sizes instead — swapped headings — or its values are
    colours of the colour master."""
    rows = _body(g, hr, list(cols.values()), cols.get('style'))
    def values(c):
        return [g.v(r, c) for r in rows if g.v(r, c) not in (None, '')]
    sizes = values(cols['size'])
    if not sizes or sum(size_like(v, mi) for v in sizes) >= 0.5 * len(sizes):
        return None
    head = W.text(g.v(hr, cols['size']))
    for key in ('color', 'style'):
        other = values(cols[key]) if key in cols else []
        if other and sum(size_like(v, mi) for v in other) >= 0.8 * len(other):
            return (f'column {letter(cols["size"])} (“{head}”) holds {_sample(sizes)}, which are not sizes, while column '
                    f'{letter(cols[key])} (“{W.text(g.v(hr, cols[key]))}”) holds the sizes ({_sample(other)}) — the headings look swapped')
    colours = (mi or {}).get('color') or set()
    if colours and sum(W.norm(v) in colours for v in sizes) >= 0.8 * len(sizes):
        return f'column {letter(cols["size"])} (“{head}”) holds colours of the colour master ({_sample(sizes)}), not sizes'
    return None


def _sizes_left_out(g, hr, first, last, rows, mi):
    """Why a size table would leave quantities out, or None: a column right next to the chosen size columns whose
    heading is another size with quantities under it (an extra "XXL" on this file), or a second set of sizes on the
    same header row with quantities under it (Delivery 1 | Delivery 2, solid | assorted packs)."""
    inside = {W.norm(g.v(hr, c)) for c in range(first, last + 1)}
    for step, start in ((1, last + 1), (-1, first - 1)):
        c = start
        while 1 <= c <= g.cols and size_like(g.v(hr, c), mi) and not isinstance(g.v(hr, c), bool) and W.norm(g.v(hr, c)) not in inside:
            if any(W.qty_value(g.v(r, c))[0] for r in rows):
                return (W.address(c, hr), g.v(hr, c), f'{W.address(c, hr)} holds the size “{show(g.v(hr, c))}” right next to the size '
                        f'columns {letter(first)}–{letter(last)}; its quantities would be left out')
            c += step
    found = [(c, g.v(hr, c)) for c in range(1, g.last_col(hr) + 1) if not first <= c <= last and size_like(g.v(hr, c), mi)
             and not isinstance(g.v(hr, c), bool) and any(W.qty_value(g.v(r, c))[0] is not None for r in rows)]
    near = {c for c, _ in found}
    found = [(c, v) for c, v in found if isinstance(v, str) or c + 1 in near or c - 1 in near]   # not a lone number (a line no.)
    if not found:
        return None
    span = W.address(found[0][0], hr) + (f':{W.address(found[-1][0], hr)}' if len(found) > 1 else '')
    return (span, W.text(found[0][1]), f'{span} also hold{"" if len(found) > 1 else "s"} sizes ({", ".join(W.text(v) for _, v in found[:8])}) '
            f'with quantities under them — a second size block (another delivery or pack?). This layout reads only '
            f'{letter(first)}–{letter(last)}, so they would be left out: put each block on its own sheet and upload again')


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
        if 'size' in cols:
            problem = _not_sizes(g, hr, cols, mi)
            if problem:
                reject('size', W.address(cols['size'], hr), g.v(hr, cols['size']), problem); del cols['size']
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
        total = next((c for c in range(first, last + 1) if W.total_heading(g.v(hr, c), Fv)), None)
        if total:                       # e.g. a saved layout's size range now ends on this file's "Total" column
            reject('size', W.address(total, hr), g.v(hr, total),
                   f'{W.address(total, hr)} holds “{W.text(g.v(hr, total))}”, a total or quantity column, inside the size columns '
                   f'{letter(first)}–{letter(last)} — it would be read as a size')
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
        left_out = _sizes_left_out(g, hr, first, last, rows, mi)
        if left_out:
            reject('size', *left_out)
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
        if isinstance(hr, int) and isinstance(col, int) and isinstance(g.head(hr, col), str):
            at = g.head_at(hr, col)                 # a merged two-row heading is anchored at its top-left cell
            out[f'table.{key}'] = dict(cell=W.address(col, hr), label_cell=W.address(at[1], at[0]), label=W.text(g.head(hr, col)))
    return out


def table_rows(found):
    return set(range(found['rows'][0], found['rows'][1] + 1)) if found else set()


def unit_info(g, table):
    if not table:
        return dict(value='', pieces_per_unit=None, evidence='no size table was found yet', cell=None)
    hr = table.get('header_row')
    if table.get('type') == 'flat':
        cols = table.get('columns') or {}
        rows = _body(g, hr, list(cols.values()), cols.get('style')) if cols else []
    else:
        rows = _body(g, hr, range(table['first_size_col'], table['last_size_col'] + 1), table.get('label_col') or table.get('color_col'))
    u = W.unit_evidence(g, table, rows=rows)          # only the rows the table reads, never a trims list further down
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
    if table and any(b['field'] in ('size', 'quantity', 'table') for b in bad):
        table['confidence'] = 'low'                    # a column of it was refused: the person picks it
        table['evidence'] = table['evidence'] + [f'refused: {b["reason"]}' for b in bad if b['field'] in ('size', 'quantity', 'table')]
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
