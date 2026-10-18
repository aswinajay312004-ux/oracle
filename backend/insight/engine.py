"""Question engine: question -> intent -> compiled SELECT -> rows -> plain answer with provenance.

Everything that touches a database is injected (executor, value finders), so the same engine runs
against Oracle in the app and against SQLite in tests.
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
import json
import re
import time
from . import catalog_qa, policy, verify
from .knowledge import norm
from .query import QueryError, compile_intent
from .understand import interpret, ai_intent, lexicon, NotUnderstood

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
    if f.get('op') == 'in' and isinstance(v, list):
        return f'{meta.get("business", f["column"])} is {" or ".join(map(str, v))}'
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
        total = (comp.get('verified_totals') or {}).get(m['key'], total) if total is not None else None   # counted once each
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
            if f['column'] != v['column']:
                continue
            if f.get('op') == 'eq' and str(f.get('value')) == str(v['value']):
                f['value'] = f'{{v{i}}}'
            elif f.get('op') == 'in' and isinstance(f.get('value'), list):   # "orders NEXT ZARA"
                f['value'] = [f'{{v{i}}}' if str(x) == str(v['value']) else x for x in f['value']]
    return out


def specialize(intent, values):
    out = dict(intent, filters=[dict(f) for f in intent.get('filters') or []])

    def fill(x, column):
        m = re.fullmatch(r'\{v(\d+)\}', str(x))
        if not m:
            return x
        i = int(m.group(1))
        if i >= len(values) or values[i]['column'] != column:
            raise LookupError
        return values[i]['value']
    try:
        for f in out['filters']:
            f['value'] = [fill(x, f['column']) for x in f['value']] if isinstance(f.get('value'), list) and f.get('op') == 'in' \
                else fill(f.get('value'), f['column'])
    except LookupError:
        return None
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


SOURCE_TEXT = {'rules': 'the question rules', 'saved example': 'a confirmed example', 'edited': 'your own changes'}


def run(intent, kb, sources, *, question='', source='rules', confidence=None, stored=None):
    """The governed answer pipeline. Each specialist step adds an entry to the answer's check trace:
    analyst (understanding) -> SQL engineer (compile from the catalog) -> security reviewer (independent SQL
    safety review) -> data quality (join check) -> Oracle (read-only run) -> data quality (total cross-check)."""
    started = time.perf_counter()
    execute, dialect = pick(kb, intent, sources)
    conf = dict(confidence or dict(score=0.9, reasons=[]))
    comp = compile_intent(intent, kb, dialect, stored=stored)   # validates every table and column first
    checks = [dict(stage='understand', role='Business analyst', status='passed' if conf.get('score', 0.9) >= 0.5 else 'warning',
                   title='Question understood', detail=f'{describe(intent, kb)} — from {SOURCE_TEXT.get(source, source)}.')]
    weak = [j for j in comp['joins'] if j['status'] in ('proposed', 'inferred')]
    checks.append(dict(stage='build', role='SQL engineer', status='warning' if weak else 'passed', title='Query built from the catalog',
                       detail=f'{len(comp["tables"])} table{"s" if len(comp["tables"]) != 1 else ""}'
                              + (f', links: {"; ".join(j["label"] + " (" + j["status"] + ")" for j in comp["joins"])}' if comp['joins'] else '')
                              + '. Values are sent separately, never written into the SQL.'))
    checks.append(policy.review(comp, kb, dialect))
    checks += verify.joins(comp, kb, execute, dialect, aggregates=any(c['kind'] == 'measure' for c in comp['columns']))
    ran = time.perf_counter()
    raw = execute(comp['sql'], comp['binds'], comp['limit'] + 1)
    comp['truncated'] = len(raw) > comp['limit']
    rows = [[cell(v) for v in r] for r in raw[:comp['limit']]]
    checks.append(dict(stage='execute', role='Oracle database expert', status='passed', title='Read-only query ran',
                       detail=f'{len(rows)}{"+" if comp["truncated"] else ""} row{"s" if len(rows) != 1 else ""} in '
                              f'{round((time.perf_counter() - ran) * 1000)} ms, '
                              + ('on this computer\'s list of uploaded files.' if dialect.name != 'oracle' or kb.table(intent['base']).get('source') == 'local'
                                 else 'on the read-only account inside a read-only transaction (changes are impossible).')))
    totals = verify.cross_check(intent, comp, rows, kb, execute, dialect, stored=stored)
    if totals:
        checks.append(totals); comp['verified_totals'] = totals.get('totals') or {}
    if weak:
        conf['score'] = round(max(0.05, conf['score'] - 0.15), 2)
        conf['reasons'] = conf.get('reasons', []) + [f'Uses an unconfirmed link: {", ".join(j["label"] for j in weak)}. An administrator can confirm it in Setup → Data dictionary.']
    failed = [c for c in checks if c['status'] == 'failed']
    unsure = [c for c in checks if c['stage'] == 'join' and c['status'] == 'warning']
    if failed or unsure:
        conf['score'] = round(max(0.05, conf['score'] - (0.4 if failed else 0.15)), 2)
        conf['reasons'] = conf.get('reasons', []) + [c['detail'] for c in failed + unsure]
    conf['level'] = 'high' if conf['score'] >= 0.75 else 'medium' if conf['score'] >= 0.5 else 'low'
    shown_binds = {k: (v.date().isoformat() if isinstance(v, datetime) else v) for k, v in comp['binds'].items()}
    return dict(question=question, understood=describe(intent, kb), intent=intent, source=source, confidence=conf, checks=checks,
                summary=summarize(intent, comp, rows, kb), columns=comp['columns'], rows=rows, row_count=len(rows),
                truncated=comp['truncated'], limit=comp['limit'], sql=comp['sql'], binds=shown_binds, notes=comp['notes'],
                tables=comp['tables'], joins=comp['joins'], graph=graph(kb, comp, intent['base']),
                elapsed_ms=round((time.perf_counter() - started) * 1000))


def _same_plan(a, b, kb, sources):
    """Two intents are the same reading when they compile to the same SQL with the same values."""
    try:
        d = pick(kb, a, sources)[1]
        ca, cb = compile_intent(a, kb, d), compile_intent(b, kb, d)
        return ca['sql'] == cb['sql'] and ca['binds'] == cb['binds']
    except Exception:
        return False


def _same_result(a, b):
    """Same answer whatever the wording: same visible values, row for row, in any order."""
    def rows(r):
        idx = [i for i, c in enumerate(r['columns']) if c['kind'] != 'key']
        return sorted((json.dumps(sorted((str(row[i]) for i in idx)), default=str) for row in r['rows']))
    return not a['truncated'] and not b['truncated'] and rows(a) == rows(b)


def gate(result):
    """A reading with low confidence is not answered with a number: the person is asked whether that is what they meant
    (spec §11: abstain rather than guess). The checked plan travels with the question, so "Yes" runs exactly it."""
    c = result.get('confidence') or {}
    if result.get('kind') or c.get('level') != 'low':
        return result
    keep = ('question', 'understood', 'intent', 'source', 'confidence', 'alternatives', 'masked', 'linked_values')
    return dict({k: result[k] for k in keep if k in result}, kind='confirm',
                message=f'I am not sure I understood. Did you mean “{result.get("understood")}”?')


def ask(question, kb, sources, *, finder, live_finder=None, examples=(), ai_chat=None, today=None, lex=None, samples=None):
    return gate(_ask(question, kb, sources, finder=finder, live_finder=live_finder, examples=examples, ai_chat=ai_chat, today=today,
                     lex=lex, samples=samples))


def _ask(question, kb, sources, *, finder, live_finder=None, examples=(), ai_chat=None, today=None, lex=None, samples=None):
    """Rules first; the local AI as a fallback or as a second reader of a weak parse. When both read the question,
    both readings run (each through every check) and are compared, as XiYan-SQL picks among candidates: the same
    result raises confidence; a different result is shown with the other reading offered as "Did you mean"."""
    stored = stored_spellings(finder)
    lex = lex or lexicon(kb)
    about_catalog = catalog_qa.answer(question, kb, lex)     # "how many tables…": the data dictionary, never order rows
    if about_catalog:
        return about_catalog
    try:
        intent, conf = interpret(question, kb, finder, live_finder, today=today, lex=lex)
    except NotUnderstood:
        if ai_chat:
            try:
                intent = ai_intent(question, kb, ai_chat, dict(tables=[]), list(examples), samples=samples)
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
    extra = dict(masked=masked, linked_values=conf['values'])
    alt = None
    if source == 'rules' and conf['score'] < 0.7 and ai_chat:
        try:
            hints = dict(tables=[intent['base']] + [f['column'].split('.')[0] for f in intent['filters']] +
                         [n['table'] for n in kb.neighbours(intent['base'])], values=conf['values'])
            alt = ai_intent(question, kb, ai_chat, hints, list(examples), samples=samples)
        except Exception:
            alt = None
    if not alt or _same_plan(alt, intent, kb, sources):
        if alt:
            conf = dict(conf, score=max(conf['score'], 0.8), reasons=conf['reasons'] + ['The question rules and the local AI read it the same way.'])
        return run(intent, kb, sources, question=question, source=source, confidence=conf, stored=stored) | extra
    ai_source = 'local AI (checked against the catalog)'
    try:
        first = run(intent, kb, sources, question=question, source=source, confidence=conf, stored=stored)
    except QueryError:   # the rules' reading cannot be answered safely: the AI's reading alone, as before
        return run(alt, kb, sources, question=question, source=ai_source, stored=stored, confidence=dict(
            conf, score=max(conf['score'], 0.6), reasons=conf['reasons'] + ['The local AI refined the question; its plan was validated before running.'])) | extra
    try:
        second = run(alt, kb, sources, question=question, source=ai_source, stored=stored, confidence=dict(conf, score=max(conf['score'], 0.6)))
    except QueryError:
        return first | extra
    if _same_result(first, second):
        c = first['confidence']; c['score'] = max(c['score'], 0.8); c['level'] = 'high' if c['score'] >= 0.75 else c['level']
        c['reasons'] = c.get('reasons', []) + [f'The local AI read it differently (“{second["understood"]}”) and got the same result.']
        return first | extra
    c = second['confidence']; c['score'] = min(c['score'], 0.6); c['level'] = 'medium' if c['score'] >= 0.5 else 'low'
    c['reasons'] = c.get('reasons', []) + [f'The question rules read it as “{first["understood"]}” and got a different result. '
                                           'If that is what you meant, choose it below.']
    second['alternatives'] = [dict(understood=first['understood'], intent=first['intent'], summary=first['summary'], source='question rules')]
    return second | extra
