"""Data-quality verification of an answer, done independently of the compiler that built it.

1. Join check (before the query runs). A link from a row to its "parent" (order line -> order, order -> buyer)
   must find at most one parent row; if the parent columns are not unique, every matching row is repeated and
   totals are counted twice. A link is safe when Oracle guarantees it (primary key, unique constraint or unique
   index on exactly those columns). Otherwise one bounded probe asks Oracle whether any parent value repeats
   (GROUP BY ... HAVING COUNT(*) > 1, first row only, short time limit); the result is remembered for an hour.
   A repeating parent stops a calculation (it would be wrong) and puts a warning on a list.
2. Total cross-check (after the query runs). For a breakdown (total by buyer, orders by month) the same
   question is asked again without the breakdown, as a separate query, and the groups must add up to that
   total (largest/smallest group must equal the overall largest/smallest). A difference is reported with both
   figures, and the overall figure is the one the answer quotes.
Each check returns an entry for the answer's "how this was checked" trace: stage, role, status
(passed / warning / failed / skipped), title, detail.
"""
from __future__ import annotations
import threading
import time

from .query import QueryError, compile_intent

PROBE_MS = 3000          # one uniqueness probe
CROSS_CHECK_MS = 5000    # the independent total
MEMO_S = 3600            # how long a probe result is trusted
_memo = {}; _lock = threading.Lock()


def _limited(execute, ms):
    return execute.limited(ms) if hasattr(execute, 'limited') else execute


def _probe(kb, execute, dialect, table, cols):
    """True when some value of cols repeats in table, False when none does, None when it could not be checked."""
    key = (dialect.name, dialect.owner, table, tuple(cols))
    with _lock:
        hit = _memo.get(key) if dialect.name == 'oracle' else None   # SQLite (tests, uploads) is cheap to ask again
    if hit and time.time() - hit[1] < MEMO_S:
        return hit[0]
    where = ' AND '.join(f'{c} IS NOT NULL' for c in cols); group = ', '.join(cols)
    sql = dialect.limit(f'SELECT 1 FROM {dialect.table(table)} WHERE {where} GROUP BY {group} HAVING COUNT(*) > 1', 1)
    try:
        result = bool(_limited(execute, PROBE_MS)(sql, {}, 1))
    except Exception:
        result = None
    with _lock:
        _memo[key] = (result, time.time())
    return result


def clear():
    with _lock:
        _memo.clear()


def joins(comp, kb, execute, dialect, aggregates):
    """Check every many-to-one link the query uses. Raises QueryError when a calculation would double count."""
    out = []
    unique = getattr(kb, 'unique', None) or {}
    by_id = {j['id']: j for j in kb.joins}
    for used in comp.get('joins') or []:
        if used['direction'] != 'up':
            continue   # one-to-many links are only used inside EXISTS or COUNT(DISTINCT ...): no repeats
        j = by_id.get(used['id'])
        if not j:
            continue
        parent, cols = j['parent'], [p for _, p in j['pairs']]
        P = kb.table(parent) or {}; name = P.get('business', parent)
        if any(k <= set(cols) for k in unique.get(parent, [])):
            out.append(dict(stage='join', role='Data quality reviewer', status='passed', title=f'Link to {name} is one-to-one',
                            detail=f'Oracle guarantees one {name.lower()} row per {"/".join(cols)} (primary key or unique index), '
                                   f'so {used["label"]} cannot repeat rows.'))
            continue
        repeats = _probe(kb, execute, dialect, parent, cols)
        if repeats is False:
            out.append(dict(stage='join', role='Data quality reviewer', status='passed', title=f'Link to {name} checked',
                            detail=f'No {"/".join(cols)} value appears twice in {parent} today, so {used["label"]} does not repeat rows. '
                                   'Oracle has no unique key here, so this is re-checked every hour.'))
        elif repeats is None:
            out.append(dict(stage='join', role='Data quality reviewer', status='warning', title=f'Link to {name} not checked',
                            detail=f'{parent} has no unique key on {"/".join(cols)} and the check did not finish in time, '
                                   'so POPPYS AI cannot promise that no row is counted twice.'))
        elif aggregates:
            raise QueryError(f'Some {"/".join(cols)} values appear more than once in {name} ({parent}), so linking it would count rows twice '
                             f'and the figure would be wrong. The answer was not calculated. An administrator can check the link '
                             f'"{used["label"]}" in Setup → Data dictionary.')
        else:
            out.append(dict(stage='join', role='Data quality reviewer', status='warning', title=f'{name} repeats some values',
                            detail=f'Some {"/".join(cols)} values appear more than once in {parent}, so some rows of this list may appear twice.'))
    return out


def cross_check(intent, comp, rows, kb, execute, dialect, stored=None):
    """Independent total for a breakdown. Returns a check entry (and 'total' per measure key when it ran)."""
    measures = [(i, c) for i, c in enumerate(comp['columns']) if c['kind'] == 'measure']
    if not intent.get('group_by') or not measures:
        return None
    if comp.get('truncated'):
        return dict(stage='totals', role='Data quality reviewer', status='skipped', title='Total not cross-checked',
                    detail=f'Only the first {comp["limit"]} groups are shown, so they cannot add up to the overall total.')
    checkable = [(i, c) for i, c in measures if c['agg'] in ('sum', 'max', 'min') or (c['agg'] == 'count' and not c.get('ref'))]
    if not checkable or not rows:
        return None
    whole = dict(intent, group_by=[], order_by=[], columns=[], limit=1)
    started = time.perf_counter()
    try:
        comp2 = compile_intent(whole, kb, dialect, stored=stored)
        raw = _limited(execute, CROSS_CHECK_MS)(comp2['sql'], comp2['binds'], 2)
    except Exception as exc:
        return dict(stage='totals', role='Data quality reviewer', status='skipped', title='Total not cross-checked',
                    detail=f'The separate total query did not finish ({str(exc)[:120]}).')
    m2 = [c for c in comp2['columns'] if c['kind'] == 'measure']
    totals = {}; problems = []; overlaps = []
    for (i, c), c2 in zip(measures, m2):
        if (i, c) not in checkable or not raw:
            continue
        overall = raw[0][comp2['columns'].index(c2)]
        values = [r[i] for r in rows if r[i] is not None]
        if overall is None or not values:
            continue
        overall = float(overall)
        combined = sum(values) if c['agg'] in ('sum', 'count') else max(values) if c['agg'] == 'max' else min(values)
        totals[c['key']] = overall
        if abs(float(combined) - overall) <= max(1e-6, abs(overall) * 1e-9):
            continue
        if c['agg'] == 'count' and combined > overall:
            overlaps.append((c['label'], combined, overall))   # one record in several groups (e.g. orders by size)
        else:
            problems.append((c['label'], combined, overall))
    if not totals:
        return None
    ms = round((time.perf_counter() - started) * 1000)
    fmt = lambda v: f'{v:,.3f}'.rstrip('0').rstrip('.')
    if problems:
        label, combined, overall = problems[0]
        return dict(stage='totals', role='Data quality reviewer', status='failed', title='Groups do not add up', totals=totals,
                    detail=f'{label}: the groups add up to {fmt(combined)} but the same question without the breakdown gives {fmt(overall)}. '
                           'Treat these figures with care and ask an administrator to check the data dictionary.')
    if overlaps:
        label, combined, overall = overlaps[0]
        return dict(stage='totals', role='Data quality reviewer', status='passed', title='Total cross-checked', totals=totals, note=True,
                    detail=f'{label}: some records belong to more than one group, so the groups add up to {fmt(combined)}; '
                           f'the real total, counted once each, is {fmt(overall)} (separate query, {ms} ms).')
    return dict(stage='totals', role='Data quality reviewer', status='passed', title='Total cross-checked', totals=totals,
                detail=f'The groups add up exactly to the total from a separate query without the breakdown ({ms} ms).')
