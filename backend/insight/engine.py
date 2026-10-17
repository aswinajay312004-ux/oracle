"""Question engine: question -> intent -> compiled SELECT -> rows -> plain answer with provenance.

Everything that touches a database is injected (executor, value finders), so the same engine runs
against Oracle in the app and against SQLite in tests.
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
import re
import time
from .knowledge import norm
from .query import compile_intent
from .understand import interpret, ai_intent, NotUnderstood

OPS_TEXT = {'eq': 'is', 'ne': 'is not', 'contains': 'contains', 'starts': 'starts with', 'gt': 'above', 'gte': 'at least',
            'lt': 'below', 'lte': 'at most', 'between': 'between', 'in': 'in'}


def cell(v):
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=' ', timespec='minutes')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, (bytes, bytearray)):
        return f'<binary, {len(v):,} bytes>'
    if hasattr(v, 'read'):   # Oracle LOB: show the beginning, never stream a whole document
        try:
            data = v.read(1, 500) if callable(getattr(v, 'read', None)) else None
        except Exception:
            return '<long text>'
        return f'<binary, {v.size():,} bytes>' if isinstance(data, (bytes, bytearray)) else data
    return v


def fmt(v):
    if isinstance(v, float):
        return f'{v:,.3f}'.rstrip('0').rstrip('.')
    if isinstance(v, int):
        return f'{v:,}'
    return str(v)


def describe_filter(f, kb):
    meta = kb.column(f['column']) or {}
    v = f.get('value')
    if f.get('op') == 'between':
        return f'{meta.get("business", f["column"])} from {v[0]} to {v[1]}'
    if f['column'].endswith('.CANCEL'):
        return 'cancelled only'
    return f'{meta.get("business", f["column"])} {OPS_TEXT.get(f.get("op", "eq"), "is")} {v}'


def describe(intent, kb):
    T = kb.table(intent['base'])
    parts = []
    for m in intent.get('measures') or []:
        if m.get('column'):
            parts.append(f'{ {"sum": "Total", "avg": "Average", "min": "Lowest", "max": "Highest", "count": "Number of"}[m["agg"]]} '
                         f'{kb.column(m["column"])["business"].lower()}')
        else:
            parts.append(f'Number of {T["plural"]}')
    if not parts:
        parts.append(f'List of {T["plural"]}')
    for g in intent.get('group_by') or []:
        ref = g if isinstance(g, str) else g['column']
        meta = kb.column(ref); bucket = None if isinstance(g, str) else g.get('bucket')
        parts.append(f'by {bucket if bucket else meta["business"].lower()}')
    text = ' '.join(parts)
    fs = [describe_filter(f, kb) for f in intent.get('filters') or []]
    return text + (' · ' + ' · '.join(fs) if fs else '')


def summarize(intent, comp, rows, kb):
    T = kb.table(intent['base'])
    fs = [describe_filter(f, kb) for f in intent.get('filters') or []]
    where = f' where {", ".join(fs)}' if fs else ''
    measures = [c for c in comp['columns'] if c['kind'] == 'measure']
    dims = [c for c in comp['columns'] if c['kind'] == 'dimension']
    if measures and not dims:
        if not rows:
            return f'No {T["plural"]} found{where}.'
        m = measures[0]; v = rows[0][comp['columns'].index(m)]
        if v is None:
            return f'No {T["plural"]} found{where}.'
        if m['agg'] == 'count' and not m.get('ref'):
            return f'{fmt(v)} {T["business"].lower() if v == 1 else T["plural"]}{where}.'
        unit = f' {m["unit"]}' if m.get('unit') else ''
        return f'{m["label"]}{where}: {fmt(v)}{unit}.'
    if measures and dims:
        if not rows:
            return f'No {T["plural"]} found{where}.'
        m = measures[0]; mi = comp['columns'].index(m); di = comp['columns'].index(dims[0])
        top = rows[0]
        unit = f' {m["unit"]}' if m.get('unit') else ''
        total = sum(r[mi] or 0 for r in rows) if m['agg'] in ('sum', 'count') else None
        text = f'{m["label"]} by {dims[0]["label"].lower()}{where}: {len(rows)} group{"s" if len(rows) != 1 else ""}.'
        text += f' Highest: {top[di] if top[di] is not None else "(blank)"} with {fmt(top[mi])}{unit}.'
        if total is not None and len(rows) > 1:
            text += f' All groups together: {fmt(total)}{unit}.'
        return text
    n = len(rows)
    if n == 0:
        return f'No {T["plural"]} found{where}.'
    more = '+' if comp.get('truncated') else ''
    if comp.get('multiplied') and any(c['kind'] == 'key' for c in comp['columns']):
        ki = next(i for i, c in enumerate(comp['columns']) if c['kind'] == 'key')
        distinct = len({r[ki] for r in rows})
        return f'Found {n}{more} {comp["multiplied"].lower()} rows across {distinct} {T["business"].lower() if distinct == 1 else T["plural"]}{where}.'
    return f'Found {n}{more} {T["business"].lower() if n == 1 else T["plural"]}{where}.'


def mask(question, spans):
    q = ' ' + norm(question) + ' '
    for i, s in enumerate(spans):
        q = q.replace(' ' + norm(s) + ' ', f' {{v{i}}} ', 1)
    return q.strip()


def generalize(intent, values):
    """Replace this question's linked values with placeholders, so the example works for other values."""
    out = dict(intent, filters=[dict(f) for f in intent.get('filters') or []])
    for f in out['filters']:
        for i, v in enumerate(values):
            if f['column'] == v['column'] and f.get('op') == 'eq' and str(f.get('value')) == str(v['value']):
                f['value'] = f'{{v{i}}}'
    return out


def specialize(intent, values):
    out = dict(intent, filters=[dict(f) for f in intent.get('filters') or []])
    for f in out['filters']:
        m = re.fullmatch(r'\{v(\d+)\}', str(f.get('value')))
        if m:
            i = int(m.group(1))
            if i >= len(values) or values[i]['column'] != f['column']:
                return None
            f['value'] = values[i]['value']
    return out


def graph(kb, comp, base):
    nodes = []
    for t in comp['tables']:
        T = kb.table(t)
        nodes.append(dict(table=t, business=T['business'], kind=T['kind'], rows=T.get('rows'), base=t == base,
                          columns=[c['label'] for c in comp['columns'] if c['table'] == t and c['kind'] != 'key']))
    edges = [dict(source=j['parent'], target=j['child'], label=j['label'], status=j['status'], on=j['on']) for j in comp['joins']]
    return dict(nodes=nodes, edges=edges)


def pick(kb, intent, sources):
    """sources: {'db': (execute, dialect), 'local': (execute, dialect)}; uploaded-file questions run locally."""
    T = kb.table(intent.get('base')) or {}
    return sources['local' if T.get('source') == 'local' else 'db']


def stored_spellings(finder):
    """(table, column, value) -> how the value index spells that value in that column ('Next', 'NEXT'), so an
    index-friendly filter on a big table matches the same rows the forgiving match would."""
    def stored(table, column, value):
        try:
            return [h['value'] for h in finder(str(value)) if h.get('table') == table and h.get('column') == column]
        except Exception:   # the value index is a help, never a reason to fail the question
            return []
    return stored


def run(intent, kb, sources, *, question='', source='rules', confidence=None, stored=None):
    started = time.perf_counter()
    execute, dialect = pick(kb, intent, sources)
    comp = compile_intent(intent, kb, dialect, stored=stored)
    raw = execute(comp['sql'], comp['binds'], comp['limit'] + 1)
    comp['truncated'] = len(raw) > comp['limit']
    rows = [[cell(v) for v in r] for r in raw[:comp['limit']]]
    conf = dict(confidence or dict(score=0.9, reasons=[]))
    weak = [j for j in comp['joins'] if j['status'] in ('proposed', 'inferred')]
    if weak:
        conf['score'] = round(max(0.05, conf['score'] - 0.15), 2)
        conf['reasons'] = conf.get('reasons', []) + [f'Uses an unconfirmed link: {", ".join(j["label"] for j in weak)}. An administrator can confirm it in Setup → Data dictionary.']
    conf['level'] = 'high' if conf['score'] >= 0.75 else 'medium' if conf['score'] >= 0.5 else 'low'
    shown_binds = {k: (v.date().isoformat() if isinstance(v, datetime) else v) for k, v in comp['binds'].items()}
    return dict(question=question, understood=describe(intent, kb), intent=intent, source=source, confidence=conf,
                summary=summarize(intent, comp, rows, kb), columns=comp['columns'], rows=rows, row_count=len(rows),
                truncated=comp['truncated'], limit=comp['limit'], sql=comp['sql'], binds=shown_binds, notes=comp['notes'],
                tables=comp['tables'], joins=comp['joins'], graph=graph(kb, comp, intent['base']),
                elapsed_ms=round((time.perf_counter() - started) * 1000))


def ask(question, kb, sources, *, finder, live_finder=None, examples=(), ai_chat=None, today=None, lex=None):
    stored = stored_spellings(finder)
    try:
        intent, conf = interpret(question, kb, finder, live_finder, today=today, lex=lex)
    except NotUnderstood:
        if ai_chat:
            try:
                intent = ai_intent(question, kb, ai_chat, dict(tables=list(kb.tables)[:0]), list(examples))
                return run(intent, kb, sources, question=question, source='local AI (checked against the catalog)', stored=stored,
                           confidence=dict(score=0.55, reasons=['The rules did not understand the question; the local AI proposed this and it was checked.']))
            except Exception:
                pass
        raise
    source = 'rules'
    masked = mask(question, conf['spans'])
    for ex in examples:
        if ex['masked'] == masked:
            special = specialize(ex['intent'], conf['values'])
            if special:
                intent = special; source = 'saved example'
                conf = dict(conf, score=max(conf['score'], 0.95), reasons=[f'Matches a confirmed example: “{ex["question"]}”.'], example_id=ex['id'])
                break
    if source == 'rules' and conf['score'] < 0.7 and ai_chat:
        try:
            hints = dict(tables=[intent['base']] + [f['column'].split('.')[0] for f in intent['filters']] +
                         [n['table'] for n in kb.neighbours(intent['base'])], values=conf['values'])
            alt = ai_intent(question, kb, ai_chat, hints, list(examples))
            intent = alt; source = 'local AI (checked against the catalog)'
            conf = dict(conf, score=max(conf['score'], 0.6), reasons=conf['reasons'] + ['The local AI refined the question; its plan was validated before running.'])
        except Exception:
            pass
    return run(intent, kb, sources, question=question, source=source, confidence=conf, stored=stored) | dict(masked=masked, linked_values=conf['values'])
