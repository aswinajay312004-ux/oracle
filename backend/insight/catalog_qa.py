"""Questions about the database itself, answered from the data dictionary (backend/insight/knowledge.py), never
from business data and never through SQL.

"How many tables are in the database?" used to be read as a count of ORDERS (audit TEST_COVERAGE_AND_PRODUCTION_RISKS
Part A: a count question that names nothing counted the default table). Catalog questions are recognised first and
answered here:
  tables   - how many tables / which tables (ERP tables visible to POPPYS AI, the local uploads list apart)
  columns  - how many columns / which columns or fields a named table has
  describe - what a named table holds
  link     - how two named tables are linked (the join path and the status of each link), or what one table links to
Anything else returns None and goes on to the business question pipeline.
"""
from __future__ import annotations
import re
import time
from .knowledge import norm

TABLE_WORDS = re.compile(r'\b(tables?|tabels?|tbls?)\b')
COLUMN_WORDS = re.compile(r'\b(columns?|fields?|attributes?)\b')
LINK_WORDS = re.compile(r'\b(link|links|linked|linking|relation|relations|relationship|relationships|related|relate|connected|'
                        r'connection|joins?|joined)\b')
DESCRIBE_WORDS = re.compile(r'\b(describe|structure|schema|definition|what is stored in|what does .+ (hold|store|contain))\b')
COUNT_WORDS = re.compile(r'\b(how many|count|number of|no\.? of|total number|ethana|ethanai)\b|எத்தனை')
DB_WORDS = re.compile(r'\b(database|db|schema|erp|oracle|system|garment[_ ]demo)\b')
STATUS_TEXT = {'verified': 'confirmed', 'declared': 'declared in Oracle', 'proposed': 'not confirmed yet', 'inferred': 'inferred, not confirmed'}


def _tables_named(text, kb, lex):
    """Tables named in the text, longest phrase first, each once, in the order they appear."""
    found = []; taken = []
    phrases = sorted((p for p, hits in lex.items() if any(k == 'table' for k, _, _ in hits)), key=len, reverse=True)
    for phrase in phrases:
        for m in re.finditer(r'(?<![a-z0-9])' + re.escape(phrase) + r'(?![a-z0-9])', text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            ref = next(r for k, r, _ in lex[phrase] if k == 'table')
            taken.append((m.start(), m.end())); found.append((m.start(), ref))
    out = []
    for _, ref in sorted(found):
        if ref not in out and kb.table(ref):
            out.append(ref)
    return out


def _result(question, understood, summary, columns, rows, reasons, tables=(), joins=(), started=None):
    cols = [dict(label=label, ref=f'catalog.{i}', kind='attribute', type='NUMBER' if kind == 'number' else 'VARCHAR2',
                 table='catalog', column=label) for i, (label, kind) in enumerate(columns)]
    return dict(kind='catalog', question=question, understood=understood, source='data dictionary', summary=summary,
                confidence=dict(score=1.0, level='high', reasons=list(reasons)),
                checks=[dict(stage='catalog', role='Data dictionary', status='passed', title='Answered from the data dictionary',
                             detail='No business data was read and no SQL was run: the answer comes from the catalog of tables, '
                                    'columns and links POPPYS AI keeps for the database.')],
                columns=cols, rows=[list(r) for r in rows], row_count=len(rows), truncated=False, limit=len(rows), sql=None, binds={},
                notes=[], tables=list(tables), joins=list(joins), graph=dict(nodes=[], edges=[]),
                elapsed_ms=round((time.perf_counter() - (started or time.perf_counter())) * 1000))


def _erp_tables(kb):
    return {n: t for n, t in kb.tables.items() if t.get('source') != 'local'}


def answer(question, kb, lex):
    """A catalog answer, or None when the question is not about the database structure."""
    started = time.perf_counter()
    text = ' ' + norm(question) + ' '
    tables_word, columns_word, link_word = TABLE_WORDS.search(text), COLUMN_WORDS.search(text), LINK_WORDS.search(text)
    describe = DESCRIBE_WORDS.search(text)
    if not (tables_word or columns_word or link_word or describe):
        return None
    named = _tables_named(text, kb, lex)
    counting = bool(COUNT_WORDS.search(text))

    if link_word and named:
        return _links(question, kb, named, started)
    if columns_word and named:
        return _columns(question, kb, named[0], counting, started)
    if describe and named:
        return _columns(question, kb, named[0], False, started, describe=True)
    if tables_word and not named and not columns_word:
        return _tables(question, kb, counting, started)
    if tables_word and named and (counting or DB_WORDS.search(text)) and not LINK_WORDS.search(text):
        # "how many tables does the order use" is about the catalog, not order rows
        return _tables(question, kb, counting, started)
    return None


def _tables(question, kb, counting, started):
    erp = _erp_tables(kb); local = [n for n in kb.tables if n not in erp]
    rows = sorted(([t['business'], n, t['kind'], t.get('rows'), STATUS_TEXT.get(t.get('status'), t.get('status') or '')]
                   for n, t in erp.items()), key=lambda r: (r[2] != 'header', r[2] != 'detail', r[1]))
    kinds = {}
    for t in erp.values():
        kinds[t['kind']] = kinds.get(t['kind'], 0) + 1
    split = ', '.join(f'{n} {k}' for k, n in sorted(kinds.items(), key=lambda x: -x[1]))
    source = 'the live database' if kb.live else 'the saved snapshot of the database structure'
    summary = (f'The ERP database has {len(erp)} tables that POPPYS AI can read ({split}), according to {source}.'
               + (f' POPPYS AI also keeps {len(local)} list of uploaded files on this computer, which is not an Oracle table.' if local else ''))
    return _result(question, 'Number of tables in the database' if counting else 'Tables in the database', summary,
                   [('Business name', 'text'), ('Table', 'text'), ('Kind', 'text'), ('Rows (statistics)', 'number'), ('Status', 'text')],
                   rows, [f'Counted the tables in the data dictionary of {source}; no order data was counted.'], tables=list(erp), started=started)


def _columns(question, kb, name, counting, started, describe=False):
    t = kb.table(name)
    cols = [(m['business'], c, m['type'], m['role'], STATUS_TEXT.get(m.get('status'), m.get('status') or ''))
            for c, m in t['columns'].items()]
    shown = [c for c in cols if c[3] != 'system']
    summary = (f'{t["business"]} ({name}) has {len(cols)} columns'
               + (f'; {len(cols) - len(shown)} are technical and not listed' if len(shown) != len(cols) else '') + '.'
               + (f' {t["description"]}' if t.get('description') else ''))
    return _result(question, f'{"Description of" if describe else "Number of columns in" if counting else "Columns of"} {t["business"].lower()} ({name})',
                   summary, [('Business name', 'text'), ('Column', 'text'), ('Type', 'text'), ('Role', 'text'), ('Status', 'text')],
                   shown, [f'Read from the data dictionary entry of {name}.'], tables=[name], started=started)


def _links(question, kb, named, started):
    if len(named) == 1:
        name = named[0]; t = kb.table(name)
        rows = [(kb.table(n['table'])['business'], n['table'], 'parent (each row points to one)' if n['direction'] == 'up' else 'child (rows point here)',
                 n['join']['label'], STATUS_TEXT.get(n['join']['status'], n['join']['status'])) for n in kb.neighbours(name)]
        summary = f'{t["business"]} ({name}) is linked to {len(rows)} table{"" if len(rows) == 1 else "s"}.'
        return _result(question, f'Tables linked to {t["business"].lower()} ({name})', summary,
                       [('Business name', 'text'), ('Table', 'text'), ('Direction', 'text'), ('Link', 'text'), ('Status', 'text')],
                       rows, ['Read from the links in the data dictionary.'], tables=[name] + [r[1] for r in rows], started=started)
    a, b = named[0], named[1]
    path = kb.path(a, b)
    ta, tb = kb.table(a), kb.table(b)
    if path is None:
        return _result(question, f'Link between {ta["business"].lower()} and {tb["business"].lower()}',
                       f'No link between {ta["business"]} ({a}) and {tb["business"]} ({b}) is known in the data dictionary.', [], [],
                       ['An administrator can propose or confirm links in Setup → Data dictionary.'], tables=[a, b], started=started)
    rows = []; here = a
    for join, direction in path:
        nxt = join['parent'] if direction == 'up' else join['child']
        rows.append((f'{kb.table(here)["business"]} → {kb.table(nxt)["business"]}', f'{here} → {nxt}',
                     ' AND '.join(f'{join["child"]}.{c} = {join["parent"]}.{p}' for c, p in join['pairs']),
                     STATUS_TEXT.get(join['status'], join['status'])))
        here = nxt
    weak = [r for r in rows if r[3] not in ('confirmed', 'declared in Oracle')]
    summary = (f'{ta["business"]} ({a}) reaches {tb["business"]} ({b}) in {len(rows)} step{"" if len(rows) == 1 else "s"}'
               + (f'; {len(weak)} of the links {"is" if len(weak) == 1 else "are"} not confirmed yet.' if weak else '; every link is confirmed.'))
    return _result(question, f'How {ta["business"].lower()} is linked to {tb["business"].lower()}', summary,
                   [('Step', 'text'), ('Tables', 'text'), ('Joined on', 'text'), ('Status', 'text')], rows,
                   ['Shortest path in the data dictionary, preferring confirmed links.'], tables=[a, b],
                   joins=[dict(on=r[2], status=r[3], label=r[0]) for r in rows], started=started)
