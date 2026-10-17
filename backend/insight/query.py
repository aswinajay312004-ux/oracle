"""Intent -> SQL compiler. The only way the question engine produces SQL.

An intent is a small JSON document (base table, columns, measures, group-by, filters, order, limit).
Every table and column is checked against the knowledge catalog, joins come from the relationship
graph (never from a model), values are bind variables, the statement is always a single SELECT with
a row limit, and aggregation is protected against fan-out (a parent quantity is never summed across
child rows; counts use COUNT(DISTINCT key)).

Text equality is index-friendly on big tables: `col IN (:as_stored, :capitals)` (or `col = :b` when both
are the same), never UPPER(TRIM(col)) = :b, which no ordinary index can answer (11 s at 3M orders against
0.002 s, docs/PERFORMANCE.md). The forgiving any-case/any-spaces form is kept for small tables only.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from .knowledge import IDENT

AGGS = {'count', 'sum', 'avg', 'min', 'max'}
OPS = {'eq', 'ne', 'contains', 'starts', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'is_null', 'not_null'}
BUCKETS = {'month', 'year', 'day'}
MAX_LIMIT = 1000
AGG_WORD = {'count': 'Number of', 'sum': 'Total', 'avg': 'Average', 'min': 'Lowest', 'max': 'Highest'}
TEXT_TYPES = ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR', 'VARCHAR')
# Tables with at most this many rows (by dictionary statistics) keep the forgiving UPPER(TRIM(col)) match: a
# full scan of them is cheap. Bigger tables, and tables without statistics (never analysed: size unknown), get
# the plain column so an index can answer. The same limit decides forgiving live lookups (service.SCAN_ROWS).
FORGIVING_ROWS = 100_000
MAX_SPELLINGS = 8   # stored spellings tried per value (as given, the value index's spellings, capitals)


def forgiving(table):
    """True when text filters on this table may use UPPER(TRIM(col)): uploaded files (in memory) and tables
    that statistics say are small."""
    if not table or table.get('source') == 'local':
        return True
    rows = table.get('rows')
    return rows is not None and rows <= FORGIVING_ROWS


class QueryError(ValueError):
    """A question that cannot be answered safely; the message is shown to the user."""


class Dialect:
    def __init__(self, name, owner=None):
        self.name = name; self.owner = owner

    def table(self, t):
        return f'{self.owner}.{t}' if self.name == 'oracle' and self.owner else t

    def limit(self, sql, n):
        return f'{sql} FETCH FIRST {int(n)} ROWS ONLY' if self.name == 'oracle' else f'{sql} LIMIT {int(n)}'

    def bucket(self, expr, unit):
        if self.name == 'oracle':
            return {'month': f"TO_CHAR({expr},'YYYY-MM')", 'year': f"TO_CHAR({expr},'YYYY')", 'day': f"TO_CHAR({expr},'YYYY-MM-DD')"}[unit]
        return {'month': f'substr({expr},1,7)', 'year': f'substr({expr},1,4)', 'day': f'substr({expr},1,10)'}[unit]

    def text(self, expr):
        return f'UPPER(TO_CHAR({expr}))' if self.name == 'oracle' else f'UPPER(CAST({expr} AS TEXT))'

    def page(self, sql, limit, offset=0):
        if self.name == 'oracle':
            return f'{sql} OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY'
        return f'{sql} LIMIT {int(limit)} OFFSET {int(offset)}'

    def date_value(self, d):
        return datetime(d.year, d.month, d.day) if self.name == 'oracle' else d.isoformat()


def _ref(text):
    t, _, c = str(text or '').partition('.')
    if not (IDENT.match(t) and IDENT.match(c)):
        raise QueryError(f'“{text}” is not a valid TABLE.COLUMN reference.')
    return t, c


def _as_date(v):
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except ValueError:
        raise QueryError(f'“{v}” is not a date (use YYYY-MM-DD).')


def _as_number(v):
    try:
        d = Decimal(str(v).replace(',', ''))
    except (InvalidOperation, ValueError):
        raise QueryError(f'“{v}” is not a number.')
    return int(d) if d == d.to_integral_value() else float(d)


class Compiled(dict):
    pass


def compile_intent(intent, kb, dialect, display_references=True, stored=None):
    """stored: optional (table, column, value) -> spellings of that value as stored (from the value index), so
    an index-friendly text filter on a big table still matches every stored spelling the value index knows."""
    base = intent.get('base')
    if not base or not IDENT.match(base) or not kb.table(base):
        raise QueryError('Choose what to look at (an order, buyer, size…).')
    T = kb.table(base)
    measures = list(intent.get('measures') or [])
    group = list(intent.get('group_by') or [])
    columns = list(intent.get('columns') or [])
    filters = list(intent.get('filters') or [])
    limit = max(1, min(int(intent.get('limit') or (200 if measures else 100)), MAX_LIMIT))
    notes = []; binds = {}; joins_used = {}

    def bind(v):
        k = f'b{len(binds)}'; binds[k] = v; return ':' + k

    # --- table instances along join paths (alias per distinct path) -----------------------------
    aliases = {(): 't0'}; from_parts = [f'{dialect.table(base)} t0']; alias_table = {'t0': base}

    def column_meta(t, c):
        meta = kb.column(f'{t}.{c}')
        if not meta:
            raise QueryError(f'{t}.{c} is not a column POPPYS AI can see.')
        return meta

    def steps_to(t, via=None):
        if via:   # explicit path: reference column -> its master
            return via
        if t == base:
            return []
        steps = kb.path(base, t)
        if steps is None:
            raise QueryError(f'{kb.table(t)["business"] if kb.table(t) else t} is not linked to {T["business"]} in the relationship map. '
                             'An administrator can add the link in Setup → Data dictionary.')
        return steps

    def join_chain(steps):
        """Joins the given steps as LEFT JOINs (select/group use); returns the final alias."""
        alias = 't0'; key = ()
        for j, direction in steps:
            key = key + ((j['id'], direction),)
            if key not in aliases:
                new = f't{len(aliases)}'
                aliases[key] = new
                target = j['parent'] if direction == 'up' else j['child']
                alias_table[new] = target
                if direction == 'up':
                    cond = ' AND '.join(f'{new}.{p} = {alias}.{c}' for c, p in j['pairs'])
                else:
                    cond = ' AND '.join(f'{new}.{c} = {alias}.{p}' for c, p in j['pairs'])
                from_parts.append(f'LEFT JOIN {dialect.table(target)} {new} ON {cond}')
                joins_used[j['id']] = dict(join=j, direction=direction)
            alias = aliases[key]
        return alias

    def has_down(steps):
        return any(d == 'down' for _, d in steps)

    def resolve(ref, expand=True):
        """ref -> (sql expression, meta, table, steps). Reference columns show their master's label."""
        t, c = _ref(ref)
        meta = column_meta(t, c)
        steps = steps_to(t)
        if expand and display_references and meta.get('references'):
            master = kb.table(meta['references'])
            j = next((j for j in kb.joins if j['child'] == t and j['parent'] == master['name'] and j['pairs'][0][0] == c and len(j['pairs']) == 1), None)
            if master and master.get('label') and j:
                msteps = steps + [(j, 'up')]
                lmeta = master['columns'][master['label']]
                return dict(steps=msteps, table=master['name'], column=master['label'], meta=dict(lmeta, business=meta['business']),
                            source_ref=f'{t}.{c}')
        return dict(steps=steps, table=t, column=c, meta=meta, source_ref=f'{t}.{c}')

    # --- measures --------------------------------------------------------------------------------
    select = []; out = []; group_exprs = []
    for g in group:
        gref = g if isinstance(g, str) else g.get('column')
        bucket = None if isinstance(g, str) else g.get('bucket')
        r = resolve(gref)
        if has_down(r['steps']) and any(m.get('agg') in ('sum', 'avg') for m in measures):
            raise QueryError(f'{T["business"]} values are stored once per {T["business"].lower()}, but each one has many '
                             f'{r["meta"]["business"].lower()} rows, so a total split by {r["meta"]["business"].lower()} would count them twice. '
                             f'Ask for a quantity stored per {r["meta"]["business"].lower()} instead (for example size quantity by size).')
        alias = join_chain(r['steps'])
        expr = f'{alias}.{r["column"]}'
        if bucket:
            if bucket not in BUCKETS:
                raise QueryError('Group dates by day, month or year.')
            expr = dialect.bucket(expr, bucket)
        name = f'c{len(select)}'
        select.append(f'{expr} AS {name}'); group_exprs.append(expr)
        out.append(dict(key=name, label=r['meta']['business'] + (f' ({bucket})' if bucket else ''), table=r['table'], column=r['column'],
                        ref=r['source_ref'], kind='dimension', role=r['meta']['role'], type=r['meta']['type']))
    for m in measures:
        agg = m.get('agg')
        if agg not in AGGS:
            raise QueryError('Unsupported calculation.')
        if not m.get('column'):
            if agg != 'count':
                raise QueryError('Choose a quantity to calculate.')
            expr = f'COUNT(DISTINCT t0.{T["key"]})' if T.get('key') else 'COUNT(*)'
            label = f'Number of {T["plural"]}'; unit = None; mref = None; mtable = base
        else:
            r = resolve(m['column'], expand=False)
            if r['steps'] and agg in ('sum', 'avg'):
                raise QueryError(f'{r["meta"]["business"]} is stored once per {kb.table(r["table"])["business"].lower()}; '
                                 f'ask about {kb.table(r["table"])["plural"]} directly so totals are not counted twice.')
            if agg in ('sum', 'avg') and r['meta']['type'] != 'NUMBER':
                raise QueryError(f'{r["meta"]["business"]} is not a number, so it cannot be totalled.')
            alias = join_chain(r['steps'])
            inner = f'{alias}.{r["column"]}'
            expr = f'COUNT(DISTINCT {inner})' if agg == 'count' else f'{agg.upper()}({inner})'
            label = f'{AGG_WORD[agg]} {r["meta"]["business"][0].lower()}{r["meta"]["business"][1:]}' if agg != 'count' else f'Number of distinct {r["meta"]["business"].lower()}'
            unit = r['meta'].get('unit'); mref = r['source_ref']; mtable = r['table']
        name = f'c{len(select)}'
        select.append(f'{expr} AS {name}')
        out.append(dict(key=name, label=label, table=mtable, column=(m.get('column') or '').partition('.')[2] or None, ref=mref,
                        kind='measure', agg=agg, unit=unit, type='NUMBER'))

    # --- list columns ------------------------------------------------------------------------------
    multiplied = None
    if not measures:
        if not columns:
            columns = [f'{base}.{c}' for c in T['display']] or [f'{base}.{T["key"]}']
        for ref in columns:
            r = resolve(ref)
            if has_down(r['steps']):
                multiplied = kb.table(r['steps'][[d for _, d in r['steps']].index('down')][0]['child'])
            alias = join_chain(r['steps'])
            name = f'c{len(select)}'
            select.append(f'{alias}.{r["column"]} AS {name}')
            out.append(dict(key=name, label=r['meta']['business'], table=r['table'], column=r['column'], ref=r['source_ref'],
                            kind='column', role=r['meta']['role'], type=r['meta']['type'], unit=r['meta'].get('unit')))
        if T.get('key'):
            select.append(f't0.{T["key"]} AS pk'); out.append(dict(key='pk', label=f'{T["business"]} record ID', table=base, column=T['key'],
                                                                    ref=f'{base}.{T["key"]}', kind='key', hidden=True, type='NUMBER'))
        if multiplied:
            notes.append(f'The list shows one row per {multiplied["business"].lower()} because a column from {multiplied["business"]} was added.')
    multiplied_name = multiplied['business'] if not measures and multiplied else None

    # --- filters -----------------------------------------------------------------------------------
    where = []; exists = {}

    def condition(expr, r, f):
        t, c = r['table'], r['column']
        spellings = (lambda v: stored(t, c, v)) if stored else None
        return _condition(expr, r['meta'], f.get('op', 'eq'), f.get('value'), bind, dialect,
                          forgiving=forgiving(kb.table(t)), stored=spellings)
    for f in filters:
        op = f.get('op', 'eq')
        if op not in OPS:
            raise QueryError('Unsupported filter.')
        r = resolve(f['column'], expand=False)
        meta = r['meta']
        if has_down(r['steps']):
            # child-table filter: EXISTS keeps one row per base record (no fan-out)
            first = [d for _, d in r['steps']].index('down')
            exists.setdefault(tuple((j['id'], d) for j, d in r['steps']), (r, first, []))[2].append(f)
            continue
        alias = join_chain(r['steps'])
        where.append(_condition(f'{alias}.{r["column"]}', meta, op, f.get('value'), bind, dialect))
    for key, (r, first, flist) in exists.items():
        outer = join_chain(r['steps'][:first])
        sub_from = []; sub_where = []; prev = outer
        for i, (j, d) in enumerate(r['steps'][first:]):
            a = f's{len(where)}_{i}'
            target = j['parent'] if d == 'up' else j['child']
            sub_from.append(f'{dialect.table(target)} {a}')
            if d == 'up':
                sub_where += [f'{a}.{p} = {prev}.{c}' for c, p in j['pairs']]
            else:
                sub_where += [f'{a}.{c} = {prev}.{p}' for c, p in j['pairs']]
            joins_used[j['id']] = dict(join=j, direction=d)
            prev = a
        for f in flist:
            sub_where.append(_condition(f'{prev}.{r["column"]}', r['meta'], f.get('op', 'eq'), f.get('value'), bind, dialect))
        target_table = kb.table(r['table'])
        if 'CANCEL' in target_table['columns'] and target_table['kind'] != 'master':
            sub_where.append(f"({prev}.CANCEL IS NULL OR {prev}.CANCEL <> 'T')")
        where.append(f'EXISTS (SELECT 1 FROM {", ".join(sub_from)} WHERE {" AND ".join(sub_where)})')
    if 'CANCEL' in T['columns'] and not intent.get('include_cancelled'):
        where.append("(t0.CANCEL IS NULL OR t0.CANCEL <> 'T')")
        notes.append(f'Cancelled {T["plural"]} are excluded.')

    # --- order --------------------------------------------------------------------------------------
    order = []
    for o in intent.get('order_by') or []:
        direction = 'DESC' if str(o.get('dir', 'desc')).lower() == 'desc' else 'ASC'
        ref = o.get('ref')
        mlist = [c for c in out if c['kind'] == 'measure']
        hit = mlist[int(ref[1:])] if re.fullmatch(r'm\d+', str(ref)) and int(ref[1:]) < len(mlist) else \
            next((c for c in out if c.get('ref') == ref or f'{c["table"]}.{c["column"]}' == ref), None)
        if hit:
            order.append(f'{hit["key"]} {direction}')
        elif ref and not measures:
            r = resolve(ref, expand=False)
            if not has_down(r['steps']):
                order.append(f'{join_chain(r["steps"])}.{r["column"]} {direction}')
    if not order:
        if measures and group:
            order = [f'{next(c["key"] for c in out if c["kind"] == "measure")} DESC']
        elif not measures:
            order = [f't0.{T["date"]} DESC'] if T.get('date') else [f't0.{T["key"]} DESC'] if T.get('key') else []
    sql = f'SELECT {", ".join(select)} FROM {" ".join(from_parts)}'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    if group_exprs and measures:
        sql += ' GROUP BY ' + ', '.join(group_exprs)
    if order:
        sql += ' ORDER BY ' + ', '.join(order)
    sql = dialect.limit(sql, limit + 1)
    used_tables = [base] + [alias_table[a] for a in sorted(alias_table, key=lambda a: int(a[1:])) if a != 't0']
    return Compiled(sql=sql, binds=binds, columns=out, limit=limit, notes=notes, tables=list(dict.fromkeys(used_tables)), multiplied=multiplied_name,
                    joins=[dict(id=v['join']['id'], child=v['join']['child'], parent=v['join']['parent'], label=v['join']['label'],
                                status=v['join']['status'], on=' AND '.join(f'{v["join"]["child"]}.{c} = {v["join"]["parent"]}.{p}' for c, p in v['join']['pairs']),
                                direction=v['direction']) for v in joins_used.values()])


def _condition(expr, meta, op, value, bind, dialect):
    kind = meta['type']
    if op == 'is_null':
        return f'{expr} IS NULL'
    if op == 'not_null':
        return f'{expr} IS NOT NULL'
    if kind == 'DATE' or kind.startswith('TIMESTAMP'):
        if op == 'between':
            a, b = value
            return f'{expr} >= {bind(dialect.date_value(_as_date(a)))} AND {expr} < {bind(dialect.date_value(_as_date(b) + timedelta(days=1)))}'
        d = _as_date(value)
        if op == 'eq':
            return f'{expr} >= {bind(dialect.date_value(d))} AND {expr} < {bind(dialect.date_value(d + timedelta(days=1)))}'
        sym = {'gt': '>=', 'gte': '>=', 'lt': '<', 'lte': '<', 'ne': '<>'}.get(op)
        if not sym:
            raise QueryError('Use on, before, after or between for dates.')
        shifted = d + timedelta(days=1) if op in ('gt', 'lte') else d
        return f'{expr} {sym} {bind(dialect.date_value(shifted))}'
    if kind == 'NUMBER' and op not in ('contains', 'starts'):
        if op == 'between':
            return f'{expr} BETWEEN {bind(_as_number(value[0]))} AND {bind(_as_number(value[1]))}'
        if op == 'in':
            return f'{expr} IN ({", ".join(bind(_as_number(v)) for v in list(value)[:50])})'
        sym = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
        return f'{expr} {sym} {bind(_as_number(value))}'
    text = f'UPPER(TRIM({expr}))' if kind != 'NUMBER' else f'UPPER({expr})'
    if op == 'in':
        return f'{text} IN ({", ".join(bind(str(v).strip().upper()) for v in list(value)[:50])})'
    if op in ('contains', 'starts'):
        v = re.sub(r'([\\%_])', r'\\\1', str(value).strip().upper())
        return f"{text} LIKE {bind(('%' if op == 'contains' else '') + v + '%')} ESCAPE '\\'"
    sym = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}.get(op)
    if not sym:
        raise QueryError('Unsupported text filter.')
    return f'{text} {sym} {bind(str(value).strip().upper())}'
