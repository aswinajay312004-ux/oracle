"""Label-anchored reuse of a saved layout (docs/DYNAMIC_TEMPLATE_AND_MAPPING_DESIGN.md).

A saved layout remembers, for every field and table column it reads, the label next to it ("PO No", "Colour", "Total").
When the next file of that layout has an extra row at the top, a column inserted, the size table lower down or a size
added, the exact-position match fails. Instead of asking the person to map the whole file again, the layout is
re-anchored: every label is looked up in the new sheet, the value is taken at the same offset from its label as before,
and the size table is found by its headings. The result is checked by the same validator as a person's mapping, and the
differences are listed ("Ship date moved from B8 to B9") for the reviewer to see before approving.

Re-anchoring refuses (returns reasons) when a label is missing or appears in more than one place it could mean, so a
different layout is never forced into a saved one.
"""
from __future__ import annotations
from copy import deepcopy
from . import workbooks as W

MIN_ANCHORS = 2          # a layout with fewer labelled anchors is not re-anchored (too little evidence)


def _cells_with(g, label):
    want = W.norm(label)
    return sorted((c['row'], c['col']) for c in g.cells.values() if isinstance(c['value'], str) and W.norm(c['value']) == want)


def _nearest(hits, old):
    """The hit closest to where the label used to be; None when two are equally close (ambiguous)."""
    if not hits:
        return None
    ranked = sorted(hits, key=lambda h: (abs(h[0] - old[0]) + abs(h[1] - old[1]), h))
    if len(ranked) > 1 and abs(ranked[0][0] - old[0]) + abs(ranked[0][1] - old[1]) == abs(ranked[1][0] - old[0]) + abs(ranked[1][1] - old[1]):
        return None
    return ranked[0]


def relocate(sheet, template, fields=None):
    """(definition, changes, reasons). definition is the template's definition moved onto this sheet, or None with the
    reasons it could not be. changes: plain sentences for every field or column that moved."""
    g = W.Grid(sheet, fields)
    d = deepcopy(template.get('definition') or {}); anchors = template.get('anchors') or {}
    labelled = {k: a for k, a in anchors.items() if a and a.get('label') and a.get('label_cell') and a.get('cell')}
    if len(labelled) < MIN_ANCHORS:
        return None, [], [f'the saved layout has {len(labelled)} labelled anchor{"s" if len(labelled) != 1 else ""}; at least {MIN_ANCHORS} are needed to find it again']
    changes = []; reasons = []; fields_out = {}; moves = []
    Fv = g.fields

    for key, ref in (d.get('fields') or {}).items():
        a = labelled.get(key)
        if not a:
            fields_out[key] = ref          # no label to follow: kept where it was, the validator decides
            continue
        old_label = W.cell_ref(a['label_cell']); old_cell = W.cell_ref(a['cell'])
        at = _nearest(_cells_with(g, a['label']), old_label)
        if at is None:
            hits = _cells_with(g, a['label'])
            reasons.append(f'the label “{a["label"]}” ({Fv.label(key)}) is ' + ('not in this file' if not hits else
                           f'in {len(hits)} places ({", ".join(W.address(c, r) for r, c in hits[:4])}); which one is meant is unclear'))
            continue
        r = at[0] + old_cell[0] - old_label[0]; c = at[1] + old_cell[1] - old_label[1]
        new = W.address(c, r); fields_out[key] = new
        if new != W.address(old_cell[1], old_cell[0]):
            moves.append((Fv.label(key), a['cell'], new, (r - old_cell[0], c - old_cell[1]), a['label'], W.address(at[1], at[0])))
    changes += _summarise(moves)
    d['fields'] = fields_out

    t = d.get('table') or {}
    table_anchors = {k[6:]: a for k, a in labelled.items() if k.startswith('table.')}
    hr = t.get('header_row')
    if table_anchors and isinstance(hr, int):
        found = {}
        for col_key, a in table_anchors.items():
            old = W.cell_ref(a['label_cell'])
            at = _nearest(_cells_with(g, a['label']), old)
            if at is None:
                reasons.append(f'the table heading “{a["label"]}” is not in this file, or not in one clear place'); continue
            found[col_key] = (at, old)
        rows = {at[0] - old[0] for at, old in found.values()}
        if len(rows) > 1:
            reasons.append('the table headings are no longer on one row')
        elif found and not reasons:
            dr = rows.pop(); new_hr = hr + dr
            if dr:
                changes.append(f'The size table moved from row {hr} to row {new_hr}')
            t['header_row'] = new_hr
            if t.get('type') == 'flat':
                for col_key, (at, old) in found.items():
                    if col_key in (t.get('columns') or {}):
                        if at[1] != t['columns'][col_key]:
                            changes.append(f'The {Fv.label(col_key).lower()} column moved from {W.address(t["columns"][col_key], 1)[:-1]} '
                                           f'to {W.address(at[1], 1)[:-1]}')
                        t['columns'][col_key] = at[1]
            else:
                _move_matrix(t, found, changes)
    elif t and not table_anchors:
        reasons.append('the saved size table has no headings to find it by')
    d['table'] = t
    if reasons:
        return None, [], reasons
    return d, changes, []


def _shift(dr, dc):
    parts = ([f'{abs(dr)} row{"s" if abs(dr) != 1 else ""} {"down" if dr > 0 else "up"}'] if dr else []) + \
            ([f'{abs(dc)} column{"s" if abs(dc) != 1 else ""} {"right" if dc > 0 else "left"}'] if dc else [])
    return ' and '.join(parts)


def _summarise(moves):
    """One sentence when every order detail moved the same way (a row inserted at the top), else one per field."""
    if len(moves) > 1 and len({m[3] for m in moves}) == 1:
        dr, dc = moves[0][3]
        return [f'All {len(moves)} order details moved {_shift(dr, dc)} (for example {moves[0][0]}: {moves[0][1]} → {moves[0][2]})']
    return [f'{label} moved from {old} to {new} (its label “{text}” is now at {at})' for label, old, new, _, text, at in moves]


def _move_matrix(t, found, changes):
    """Colour / garment / total columns follow their headings; the size columns keep their distance from the column
    before them and from the total after them, so an added size (a new XXL before Total) widens the range."""
    old = {k: t.get(k) for k in ('label_col', 'color_col', 'total_col', 'first_size_col', 'last_size_col')}
    for key in ('label_col', 'color_col', 'total_col'):
        if key in found and t.get(key):
            t[key] = found[key][0][1]
    lead = next((k for k in ('color_col', 'label_col') if k in found and old.get(k)), None)
    if lead and old.get('first_size_col'):
        t['first_size_col'] = old['first_size_col'] + (t[lead] - old[lead])
    if 'total_col' in found and old.get('total_col') and old.get('last_size_col'):
        t['last_size_col'] = t['total_col'] - (old['total_col'] - old['last_size_col'])
    elif lead and old.get('last_size_col'):
        t['last_size_col'] = old['last_size_col'] + (t[lead] - old[lead])
    for key, word in (('color_col', 'colour'), ('label_col', 'garment'), ('total_col', 'total')):
        if old.get(key) and t.get(key) != old[key]:
            changes.append(f'The {word} column moved from {W.address(old[key], 1)[:-1]} to {W.address(t[key], 1)[:-1]}')
    old_n = (old.get('last_size_col') or 0) - (old.get('first_size_col') or 0)
    new_n = (t.get('last_size_col') or 0) - (t.get('first_size_col') or 0)
    if new_n != old_n:
        changes.append(f'The size table now has {new_n + 1} size columns (the saved layout had {old_n + 1})')
