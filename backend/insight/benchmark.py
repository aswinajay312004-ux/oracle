"""Ask AI accuracy benchmark: real business questions, each with an answer SQL written by hand, independently
of the question engine (idea from Dataherald's "golden SQL" and execution-accuracy evaluation).

Each golden question is asked exactly as a user would ask it. Its hand-written SQL is run on the same database
through the same read-only executor (and so through the same safety review), and the two results are
compared. The report says, per question: correct, wrong (with both results), refused, not understood or error.
This is how a change to the rules, the catalog or the local model is shown to make answers better, not worse.

Golden file format (JSON list):
  
   "sql": "SELECT b.BUYERNAME, SUM(o.OQTY) FROM {ORDMAS} o JOIN {BUYMAS} b ON ... GROUP BY b.BUYERNAME",
   "compare": "rows" | "ordered" | "scalar" | "values",
   "params": {"order": "SELECT DOCID FROM {ORDMAS} WHERE ... ORDER BY ORDMASID DESC"}}   (optional)
{TABLE} is replaced by the owner-qualified table, {MONTH(expr)} / {YEAR(expr)} by the dialect's bucket.
"sql_oracle" / "sql_sqlite" replace "sql" on that database (row limits are written differently).
A param's query picks a real value (first row, first column) from the database; {param} in the question and
:param in the SQL then use it, so the same file works on any copy of the ERP.
compare: rows = same rows in any order; ordered = same rows in the same order; scalar = the first number of
the answer; values = the hand-written SQL's single column equals one column of the answer (lists).
"""
from __future__ import annotations
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .query import QueryError
from .understand import NotUnderstood

COMPARES = {'rows', 'ordered', 'scalar', 'values'}
MAX_ROWS = 1000
_PLACE = re.compile(r'\{(MONTH|YEAR)\(([^()]+)\)\}|\{([A-Z][A-Z0-9_$#]*)\}')


def load(*paths):
    out = []
    for p in map(Path, paths):
        if p.exists():
            out += load_items(json.loads(p.read_text(encoding='utf-8')), p.name, {q['id'] for q in out})
    return out


def load_items(items, name, seen=()):
    out = []; seen = set(seen)
    for q in items:
        if not isinstance(q, dict) or not q.get('question') or not q.get('sql') or q.get('compare', 'rows') not in COMPARES:
            raise ValueError(f'{name}: every golden question needs "question", "sql" and a known "compare" ({", ".join(sorted(COMPARES))}).')
        q = dict(q, id=str(q.get('id') or len(seen) + 1), compare=q.get('compare', 'rows'), source=name)
        if q['id'] in seen:
            raise ValueError(f'{name}: golden question id "{q["id"]}" is used twice.')
        seen.add(q['id']); out.append(q)
    return out


def expand(sql, dialect):
    def sub(m):
        if m.group(1):
            return dialect.bucket(m.group(2).strip(), m.group(1).lower())
        return dialect.table(m.group(3))
    return _PLACE.sub(sub, sql)


def _norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return round(f, 4) if f != int(f) else int(f)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=' ', timespec='minutes')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        try:   # numbers that came back as text (SQLite) compare as numbers
            return _norm(Decimal(s)) if re.fullmatch(r'-?\d+(\.\d+)?', s) else s
        except Exception:
            return s
    return v


def _row(values):
    """Column order is not part of correctness (an answer may show the buyer before or after the total)."""
    return tuple(sorted((_norm(v) for v in values), key=lambda x: (x is None, type(x).__name__, str(x))))


def _visible(result):
    idx = [i for i, c in enumerate(result['columns']) if not c.get('hidden') and c['kind'] != 'key']
    return [[r[i] for i in idx] for r in result['rows']], [result['columns'][i] for i in idx]


def compare(kind, expected, result):
    """(correct, explanation). expected: rows from the hand-written SQL; result: an engine answer."""
    rows, cols = _visible(result)
    if result.get('truncated') and kind != 'ordered':   # "top 5 buyers" is cut on purpose
        return None, f'The answer shows the first {result["limit"]} rows only, so it cannot be compared completely.'
    if kind == 'scalar':
        want = _norm(expected[0][0]) if expected and expected[0] else None
        mi = next((i for i, c in enumerate(cols) if c['kind'] == 'measure'), 0)
        got = _norm(rows[0][mi]) if rows and rows[0] else None
        if want is None and got is None or not rows and want in (0, None):
            return True, 'Both empty.'
        return got == want, f'expected {want}, got {got}'
    if kind == 'values':
        want = sorted({_norm(r[0]) for r in expected}, key=str)
        for i, c in enumerate(cols):
            if sorted({_norm(r[i]) for r in rows}, key=str) == want:
                return True, f'{len(want)} value(s) match in “{c["label"]}”.'
        return False, f'expected the values {want[:10]}{"…" if len(want) > 10 else ""}; no answer column holds exactly these.'
    want = [_row(r) for r in expected]; got = [_row(r) for r in rows]
    if kind == 'ordered':
        ok = want == got
    else:
        ok = sorted(want, key=str) == sorted(got, key=str)
    if ok:
        return True, f'{len(want)} row(s) match.'
    missing = [w for w in want if w not in got][:3]; extra = [g for g in got if g not in want][:3]
    return False, f'expected {len(want)} row(s), got {len(got)}' + (f'; missing {missing}' if missing else '') + (f'; unexpected {extra}' if extra else '')


def run(questions, ask, execute, dialect):
    """ask(question) -> engine answer (raises NotUnderstood / QueryError). execute/dialect: the read-only source
    the hand-written SQL runs on (the same database the answers come from)."""
    items = []; started = time.perf_counter()
    for q in questions:
        item = dict(id=q['id'], question=q['question'], compare=q['compare'], source=q.get('source'))
        try:
            binds = {}
            for name, sql in (q.get('params') or {}).items():
                found = execute(dialect.limit(expand(sql, dialect), 1), {}, 1)
                if not found or found[0][0] is None:
                    raise LookupError(f'No value for {{{name}}}: the database has no rows for it.')
                binds[name] = found[0][0]
            question = q['question'].format(**{k: str(v).strip() for k, v in binds.items()})
            item['question'] = question
            sql = q.get(f'sql_{dialect.name}') or q['sql']
            used = {k: v for k, v in binds.items() if f':{k}' in sql}
            expected = execute(expand(sql, dialect), used, MAX_ROWS + 1)
        except LookupError as exc:
            items.append(dict(item, status='skipped', detail=str(exc))); continue
        except Exception as exc:
            items.append(dict(item, status='error', detail=f'The hand-written SQL failed: {str(exc)[:200]}')); continue
        t0 = time.perf_counter()
        try:
            answer = ask(question)
        except NotUnderstood as exc:
            items.append(dict(item, status='not understood', detail=str(exc)[:200])); continue
        except QueryError as exc:
            items.append(dict(item, status='refused', detail=str(exc)[:200])); continue
        except Exception as exc:
            items.append(dict(item, status='error', detail=f'{type(exc).__name__}: {str(exc)[:200]}')); continue
        ok, why = compare(q['compare'], expected, answer)
        failed = [c['title'] for c in answer.get('checks') or [] if c['status'] == 'failed']
        items.append(dict(item, status='skipped' if ok is None else 'correct' if ok else 'wrong', detail=why, understood=answer.get('understood'),
                          confidence=(answer.get('confidence') or {}).get('level'), checks_failed=failed, source_used=answer.get('source'),
                          sql=answer.get('sql'), ms=round((time.perf_counter() - t0) * 1000)))
    counted = [i for i in items if i['status'] != 'skipped']
    correct = sum(i['status'] == 'correct' for i in counted)
    return dict(total=len(counted), correct=correct, accuracy=round(correct / len(counted), 4) if counted else None,
                by_status={s: sum(i['status'] == s for i in items) for s in ('correct', 'wrong', 'refused', 'not understood', 'error', 'skipped')},
                seconds=round(time.perf_counter() - started, 1), items=items)
