"""Excel -> structured JSON without losing anything, and where every Excel cell ends up.

Two representations are kept for every upload (the original .xlsx file itself is stored unchanged as well):
  1. the workbook representation: every populated cell with its sheet, address, value, number format, formula and
     merged ranges (workbooks.inspect)
  2. this business JSON: order details (label -> value), every Excel table (title, headers, rows), the parsed
     order(s) and every remaining cell. Each populated cell appears exactly once, so nothing is silently dropped.

coverage() then says, for every populated cell, whether its value goes to Oracle (and to which table.column), is the
label of a value that goes to Oracle, or is kept in the JSON only ("Preserved — not mapped to Oracle")."""
from __future__ import annotations
from .entry import Book, GLOBAL_LABELS, excel_pairs, find_sections, shown
from .vocabulary import SECTION_GROUP, group_of
from .workbooks import norm, text

KIND_WORDS = [('fabric', ('fabric', 'yarn', 'gsm', 'knit')), ('printing', ('print', 'ink', 'screen')), ('embroidery', ('emb', 'embroidery')),
              ('costing', ('cost', 'price', 'costing', 'rate')), ('packing', ('carton', 'poly', 'bag', 'gum', 'sticker', 'hook', 'pack')),
              ('trims', ('label', 'prong', 'prongs', 'tape', 'thread', 'bullet', 'ticket', 'paper', 'button', 'zip', 'snap', 'ht', 'trim'))]


def kind_of(title):
    n = norm(title)
    if n.startswith('size block'):
        return 'order'
    return next((k for k, words in KIND_WORDS if any(w in n.split() or n.startswith(w) for w in words)), 'other')


def document(draft):
    """The upload as structured JSON. Every populated cell is placed exactly once."""
    sheets = draft['sheets']
    book = Book(sheets)
    placed = set()
    out = dict(file=dict(name=draft.get('filename'), sha256=draft.get('filehash'), format=draft.get('format'),
                         sheets=[dict(name=s['name'], order=i + 1, hidden=s['hidden'], rows=s['rows'], cols=s['cols'], cells=len(s['cells']),
                                      merged=s.get('merged', [])) for i, s in enumerate(sheets)]),
               order_details=[], tables=[], orders=[], other_cells=[])
    for t in draft.get('sections') or find_sections(sheets):
        cells = book.cells.get(t['sheet'], {})
        heads = {h['col']: h['text'] for h in t['headers']}
        rows = []
        for r in range(t['header_row'] + 1, t['end_row'] + 1):
            own = {c: cells[(r, c)] for c in range(t['first_col'], t['last_col'] + 1) if (r, c) in cells}
            if not own:
                continue
            values = {}
            for c, cell in own.items():
                key = heads.get(c) or f'column {cell["cell"].rstrip("0123456789")}'
                if key in values:
                    key = f'{key} ({cell["cell"]})'
                values[key] = shown(cell['value'])
                placed.add((t['sheet'], cell['cell']))
            first = next(iter(own.values()))
            rows.append(dict(excel_row=r, total=norm(first['value']) in ('total', 'grand total'), values=values,
                             cells={k: c['cell'] for k, c in zip(values, own.values())}))
        for c in range(t['first_col'], t['last_col'] + 1):
            if (t['header_row'], c) in cells:
                placed.add((t['sheet'], cells[(t['header_row'], c)]['cell']))
        if t.get('title_cell'):
            placed.add((t['sheet'], t['title_cell']))
            title_row = t.get('title_row')
            for (r, c), cell in cells.items():
                if r == title_row:
                    placed.add((t['sheet'], cell['cell']))
        out['tables'].append(dict(id=t['id'], title=t['title'], kind=kind_of(t['title']), sheet=t['sheet'], header_row=t['header_row'],
                                  columns=[h['text'] for h in t['headers']], rows=rows,
                                  note=' '.join(text(cells[(t['title_row'], c)]['value']) for (r, c) in sorted(cells) if r == t.get('title_row') and cells[(r, c)]['cell'] != t.get('title_cell')) or None))
    for p in excel_pairs(book, GLOBAL_LABELS):
        keys = {(p['sheet'], p['cell']), (p['sheet'], p['label_cell'])}
        if keys & placed:
            continue
        placed |= keys
        out['order_details'].append(dict(label=p['label'], value=p['value'], sheet=p['sheet'], cell=p['cell'], label_cell=p['label_cell']))
    for o in draft.get('orders') or []:
        out['orders'].append({k: o.get(k) for k in ('order_ref', 'buyer', 'style', 'description', 'po_number', 'ship_date', 'order_date', 'season',
                                                    'merch', 'quantity_basis', 'pieces_per_unit')} | dict(
            lines=[dict(garment=l.get('label'), colour=l.get('color'), excel_total=l.get('declared_total'),
                        sizes=[dict(size=s.get('size'), quantity=s.get('quantity'), cell=(s.get('source') or {}).get('cell')) for s in l.get('sizes', [])])
                   for l in o.get('lines', [])],
            sources={k: f"{v.get('sheet')}!{v.get('cell')}" for k, v in (o.get('provenance') or {}).items() if isinstance(v, dict) and v.get('cell')}))
    for s in sheets:
        for c in sorted(s['cells'], key=lambda x: (x['row'], x['col'])):
            if (s['name'], c['cell']) not in placed:
                out['other_cells'].append(dict(sheet=s['name'], cell=c['cell'], value=shown(c['value']), formula=c.get('formula')))
                placed.add((s['name'], c['cell']))
    total = sum(len(s['cells']) for s in sheets)
    out['counts'] = dict(cells=total, in_order_details=sum(1 + (d['cell'] != d['label_cell']) for d in out['order_details']),
                         in_tables=sum(len(r['values']) for t in out['tables'] for r in t['rows']), other=len(out['other_cells']),
                         accounted=len(placed), missing=total - len(placed))
    return out


def coverage(draft, plan):
    """Where every populated cell ends up: {sheet: {address: [state, group, targets]}} plus counts.
    state: 'mapped' (value saved to Oracle), 'label' (names a saved value), 'kept' (in the JSON, not mapped to Oracle)."""
    book = Book(draft['sheets'])
    targets = {}
    for op in plan.get('operations', []):
        for col, info in (op.get('columns') or {}).items():
            src = info.get('source') or {}
            if src.get('cell') and src.get('sheet') and info.get('origin') in ('excel', 'ai', 'link'):
                key = (src['sheet'], src['cell'])
                ref = f"{op['table']}.{col}"
                if ref not in targets.setdefault(key, []):
                    targets[key].append(ref)
    out, counts = {}, dict(mapped=0, label=0, kept=0)
    labels = {}
    for p in excel_pairs(book, GLOBAL_LABELS):
        if (p['sheet'], p['cell']) in targets:
            labels[(p['sheet'], p['label_cell'])] = targets[(p['sheet'], p['cell'])]
    table_of = {}
    for t in draft.get('sections') or []:
        for r in range(t.get('title_row') or t['header_row'], t['end_row'] + 1):
            for c in range(t['first_col'], t['last_col'] + 1):
                table_of[(t['sheet'], r, c)] = t
    header_targets = {}
    for t, info in (plan.get('tables') or {}).items():
        sec = info.get('section')
        if not sec:
            continue
        for v in plan.get('orders', []):
            for col, b in (v['tables'].get(t, {}).get('boxes') or {}).items():
                bb = b.get('binding') or {}
                if bb.get('kind') == 'header':
                    header_targets[(sec['sheet'], sec['header_row'], bb['col'])] = f'{t}.{col}'
    for s in draft['sheets']:
        sheet = {}
        for c in s['cells']:
            key = (s['name'], c['cell'])
            t = table_of.get((s['name'], c['row'], c['col']))
            if key in targets:
                refs = targets[key]; state = 'mapped'; group = group_of(refs[0].split('.', 1)[1])
            elif key in labels:
                refs = labels[key]; state = 'label'; group = group_of(refs[0].split('.', 1)[1])
            elif t and (s['name'], t['header_row'], c['col']) in header_targets and c['row'] == t['header_row']:
                refs = [header_targets[(s['name'], t['header_row'], c['col'])]]; state = 'label'; group = group_of(refs[0].split('.', 1)[1])
            else:
                refs = []; state = 'kept'
                group = SECTION_GROUP.get(kind_of(t['title']), 'other') if t else 'other'
            counts[state] += 1
            sheet[c['cell']] = [state, group, refs]
        out[s['name']] = sheet
    return dict(cells=out, counts=counts)
