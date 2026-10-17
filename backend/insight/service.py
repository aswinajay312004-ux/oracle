"""I/O for the data intelligence layer: dictionary harvesting (bounded, statistics instead of COUNT(*)),
the knowledge cache, the value index, read-only executors and the local 'uploaded files' source.
Sizes and time limits below were measured on a 2.4M / 24M-row replica (docs/PERFORMANCE.md)."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from .. import local_store as store
from ..config import DATA, OWNER, ROOT, setting
from .knowledge import build, Knowledge, norm, IDENT
from .query import Dialect, QueryError


def _ms(key, default_s):
    """A time limit in milliseconds from .env.poppys / POPPYS_<KEY> (seconds there), never below 1 s."""
    try:
        return max(1000, int(float(setting(key, str(default_s))) * 1000))
    except ValueError:
        return default_s * 1000


SNAPSHOT = ROOT / 'docs' / 'live_schema_columns.json'
CACHE = 'knowledge_physical.json'
VALUE_META = 'value_index_meta.json'
VALUE_CAP = 20000          # distinct values indexed per column (the newest ones when a column has more)
BIG_TABLE = 2_000_000      # detail tables bigger than this are looked up live instead of indexed
SCAN_ROWS = 100_000        # above this, live lookups use index-friendly col = :v only (no UPPER/TRIM full scans)
TIMEOUT_MS = _ms('QUERY_TIMEOUT_S', 20)   # any one read-only query (QUERY_TIMEOUT_S=30 in .env.poppys to allow more)
LOOKUP_MS = 2500           # one live lookup on a big table
LOOKUP_BUDGET_MS = 6000    # all live lookups for one word together
TEXT = ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR', 'VARCHAR')
TIMED_OUT = ('DPY-4024', 'ORA-01013', 'ORA-03156')   # call timeout / cancelled by the time limit
_memo = {}; _lock = threading.RLock()


class TimedOut(QueryError):
    """A read that hit its time limit. The message is shown as-is (Ask AI 'refused', explorer 409)."""

    def __init__(self, seconds):
        super().__init__(f'The database needed more than {seconds:g} seconds for this, so POPPYS AI stopped the query to keep '
                         'the app responsive. Narrow it down (an order reference, a buyer, a date range or an exact match) and try again.')


def timed_out(exc):
    code = getattr(exc.args[0], 'full_code', '') if getattr(exc, 'args', None) else ''
    return code in TIMED_OUT or any(str(exc).startswith(c) for c in TIMED_OUT)


def limited(execute, timeout_ms):
    """The executor with a tighter time limit when it supports one (Oracle); other executors run unchanged."""
    return execute.limited(timeout_ms) if hasattr(execute, 'limited') else execute

LOCAL_TABLE = 'UPLOADS'
LOCAL_COLUMNS = {
    'FILENAME': ('VARCHAR2', 'File name', ['file', 'file name', 'filename']),
    'FILEKIND': ('VARCHAR2', 'File type', ['file type', 'excel']),
    'FORMAT': ('VARCHAR2', 'Detected layout', ['layout', 'format', 'template']),
    'UPLOADED_BY': ('VARCHAR2', 'Uploaded by', ['uploaded by', 'user']),
    'UPLOADED_ON': ('DATE', 'Upload date', ['upload date', 'uploaded on', 'uploaded']),
    'STATUS': ('VARCHAR2', 'Status', ['status', 'state']),
    'BUYER': ('VARCHAR2', 'Buyer in file', ['buyer']),
    'STYLE': ('VARCHAR2', 'Style in file', ['style']),
    'ORDER_REF': ('VARCHAR2', 'Order reference', ['order reference']),
    'PIECES': ('NUMBER', 'Pieces in file', ['pieces', 'quantity']),
}


# --- physical catalog ---------------------------------------------------------------------------
def harvest(conn):
    """Read the data dictionary of the owner. Bounded queries only (no COUNT(*) on data tables)."""
    c = conn.cursor(); physical = {}
    c.execute('''select table_name,column_name,data_type,data_length,data_precision,data_scale,nullable
                 from all_tab_columns where owner=:o order by table_name,column_id''', o=OWNER)
    for t, col, kind, length, prec, scale, null in c:
        physical.setdefault(t, {})[col] = dict(type=kind, length=length, precision=prec, scale=scale, nullable=null)
    c.execute('select table_name,num_rows,last_analyzed from all_tables where owner=:o', o=OWNER)
    stats = {t: n for t, n, _ in c}
    comments = {}
    c.execute('select table_name,comments from all_tab_comments where owner=:o and comments is not null', o=OWNER)
    comments.update({t: x for t, x in c})
    c.execute('select table_name,column_name,comments from all_col_comments where owner=:o and comments is not null', o=OWNER)
    comments.update({f'{t}.{col}': x for t, col, x in c})
    c.execute('''select a.constraint_name,a.table_name,ac.column_name,ac.position,r.table_name,rc.column_name
                   from all_constraints a
                   join all_cons_columns ac on ac.owner=a.owner and ac.constraint_name=a.constraint_name
                   join all_constraints r on r.owner=a.r_owner and r.constraint_name=a.r_constraint_name
                   join all_cons_columns rc on rc.owner=r.owner and rc.constraint_name=r.constraint_name and rc.position=ac.position
                  where a.owner=:o and a.constraint_type='R' order by a.constraint_name,ac.position''', o=OWNER)
    fks = {}
    for name, child, ccol, _, parent, pcol in c:
        fk = fks.setdefault(name, dict(child=child, parent=parent, pairs=[]))
        fk['pairs'].append([ccol, pcol])
    # Leading index columns: where an exact lookup is cheap on a big table (live_finder probes these first).
    c.execute('select table_name,column_name from all_ind_columns where table_owner=:o and column_position=1', o=OWNER)
    indexed = sorted({f'{t}.{col}' for t, col in c})
    return dict(physical=physical, stats=stats, comments=comments, fks=list(fks.values()), indexed=indexed, live=True,
                harvested_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def snapshot():
    physical = {}
    for r in json.loads(SNAPSHOT.read_text(encoding='utf-8')):
        physical.setdefault(r['table'], {})[r['column']] = dict(type=r['type'], length=r['length'], precision=r['precision'],
                                                                 scale=r['scale'], nullable=r['nullable'])
    return dict(physical=physical, stats={}, comments={}, fks=[], indexed=[], live=False, harvested_at=None)


def _read_cache():
    try:
        return json.loads((DATA / CACHE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def refresh_physical(connect):
    with connect() as conn:
        conn.call_timeout = 60000
        data = harvest(conn)
    (DATA / CACHE).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    with _lock:
        _memo.clear()
    return data


_failed = {'at': 0.0}


def physical(connect=None):
    """Cached harvest, else a live harvest, else the committed snapshot (marked offline).
    After a failed connection, retries at most once a minute so offline pages stay fast."""
    import time
    data = _read_cache()
    if data:
        return data
    if connect and time.time() - _failed['at'] > 60:
        try:
            return refresh_physical(connect)
        except Exception:
            _failed['at'] = time.time()
    return snapshot()


def local_physical():
    return {LOCAL_TABLE: {c: dict(type=t, length=200, precision=None, scale=None, nullable='Y') for c, (t, _, _) in LOCAL_COLUMNS.items()}}


def local_curated():
    return dict(business='Uploaded file', plural='uploaded files', kind='local', description='Files uploaded to POPPYS AI (this computer).',
                synonyms=['upload', 'uploads', 'uploaded file', 'uploaded files', 'file', 'files', 'import', 'imports', 'excel file', 'excel files', 'workbooks'],
                label='FILENAME', date='UPLOADED_ON', display=['FILENAME', 'UPLOADED_ON', 'UPLOADED_BY', 'BUYER', 'STYLE', 'ORDER_REF', 'STATUS'],
                columns={c: dict(business=b, synonyms=s, role='date' if t == 'DATE' else 'measure' if c == 'PIECES' else 'label' if c == 'FILENAME' else 'attribute',
                                 searchable=c in ('UPLOADED_BY', 'BUYER', 'STYLE', 'STATUS', 'FILEKIND'))
                         for c, (t, b, s) in LOCAL_COLUMNS.items()})


def knowledge(connect=None, mapping=None, refresh=False):
    if refresh and connect:
        refresh_physical(connect)
    phys = physical(connect)
    overrides = store.knowledge_overrides()
    memo_key = json.dumps([phys.get('harvested_at'), phys.get('live'), overrides], sort_keys=True, default=str)
    with _lock:
        if _memo.get('key') == memo_key and _memo.get('kb'):
            return _memo['kb']
    kb = build_kb(phys, mapping, overrides)
    with _lock:
        _memo.update(key=memo_key, kb=kb)
    return kb


def build_kb(phys, mapping=None, overrides=None):
    from .knowledge import curated
    cur = curated()
    cur['tables'][LOCAL_TABLE] = local_curated()
    data = build({**phys['physical'], **local_physical()}, mapping=mapping, curated_data=cur, overrides=overrides or {},
                 stats=phys.get('stats'), comments=phys.get('comments'), fks=phys.get('fks'), live=phys.get('live', False))
    data['tables'][LOCAL_TABLE]['source'] = 'local'
    kb = Knowledge(data)
    kb.indexed = set(phys['indexed']) if 'indexed' in phys else None   # None: harvested before index info was kept
    return kb


def invalidate():
    with _lock:
        _memo.clear()


# --- executors ------------------------------------------------------------------------------------
def _guard(sql):
    s = sql.lstrip().upper()
    if not s.startswith('SELECT') or ';' in sql:
        raise ValueError('Only single SELECT statements may run.')


class OracleExecutor:
    """Read-only execution: SET TRANSACTION READ ONLY, call timeout, bounded fetch, always rolled back.
    A query that hits its time limit raises TimedOut (a plain-English QueryError); the connection stays usable."""

    def __init__(self, conn, timeout_ms=TIMEOUT_MS):
        self.conn = conn; self.timeout_ms = timeout_ms; conn.call_timeout = timeout_ms

    def __call__(self, sql, binds, max_rows, timeout_ms=None):
        _guard(sql)
        c = self.conn.cursor()
        limit = int(timeout_ms or self.timeout_ms)
        self.conn.call_timeout = limit
        try:
            c.execute('SET TRANSACTION READ ONLY')
            c.arraysize = max(50, min(int(max_rows), 1000))
            c.execute(sql, binds)
            return c.fetchmany(int(max_rows))
        except Exception as exc:
            if timed_out(exc):
                raise TimedOut(limit / 1000) from None
            raise
        finally:
            self.conn.call_timeout = self.timeout_ms
            self.conn.rollback()

    def limited(self, timeout_ms):
        return lambda sql, binds, max_rows: self(sql, binds, max_rows, timeout_ms=min(int(timeout_ms), self.timeout_ms))


class SqliteExecutor:
    def __init__(self, conn):
        self.conn = conn

    def __call__(self, sql, binds, max_rows):
        _guard(sql)
        return self.conn.execute(sql, binds).fetchmany(int(max_rows))


def uploads_db():
    """In-memory table of the uploaded files (the local document source)."""
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.execute(f'CREATE TABLE {LOCAL_TABLE} ({", ".join(LOCAL_COLUMNS)})')
    rows = []
    for d in store.summaries():
        rows.append((d.get('filename'), (d.get('kind') or 'excel').upper(), d.get('format'), d.get('uploaded_by'),
                     (d.get('created_at') or '')[:10], d.get('status'), ', '.join(x for x in d.get('buyers', []) if x) or None,
                     ', '.join(x for x in d.get('styles', []) if x) or None, ', '.join(x for x in d.get('order_refs', []) if x) or None,
                     d.get('pieces')))
    conn.executemany(f'INSERT INTO {LOCAL_TABLE} VALUES ({",".join("?" * len(LOCAL_COLUMNS))})', rows)
    return conn


# --- value index ------------------------------------------------------------------------------------
def indexable(kb):
    """Searchable text columns. Very big detail tables are left to live lookups; masters and the order header
    are always indexed (their categories and references are what questions name)."""
    for name, t in kb.tables.items():
        if t.get('source') == 'local':
            continue
        big = (t.get('rows') or 0) > BIG_TABLE
        for c, m in t['columns'].items():
            if m.get('searchable') and m['type'] in ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'VARCHAR') and IDENT.match(c):
                if big and t['kind'] not in ('master', 'header'):
                    continue
                yield name, c


def refresh_values(kb, execute, dialect):
    """Distinct values of searchable columns, at most VALUE_CAP per column. A column with more distinct values is
    'partial': it keeps the values of its newest rows (read through the record-ID index, so cheap on any size),
    and only partial columns are looked up live for codes the index has not seen (see lookup_plan)."""
    rows = []; done = []; skipped = []; partial = []; started = time.time()
    for t, c in indexable(kb):
        meta = kb.table(t); key = meta.get('key')
        try:
            found = execute(dialect.limit(f'SELECT {c}, COUNT(*) FROM {dialect.table(t)} WHERE {c} IS NOT NULL GROUP BY {c}', VALUE_CAP + 1),
                            {}, VALUE_CAP + 1)
            if len(found) > VALUE_CAP:
                partial.append(f'{t}.{c}')
                newest = dialect.limit(f'SELECT {c} FROM {dialect.table(t)} WHERE {c} IS NOT NULL ORDER BY {key} DESC', VALUE_CAP)
                found = execute(f'SELECT {c}, COUNT(*) FROM ({newest}) x GROUP BY {c}', {}, VALUE_CAP) if \
                    key and meta['columns'].get(key, {}).get('type') == 'NUMBER' else found[:VALUE_CAP]
            for value, n in found:
                v = str(value).strip()
                if v and len(v) <= 120:
                    rows.append((t, c, v, norm(v), int(n)))
            done.append(f'{t}.{c}')
        except Exception as exc:
            skipped.append(f'{t}.{c}: {str(exc)[:80]}')
    store.replace_values(rows)
    meta = dict(refreshed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'), complete=sorted(set(done) - set(partial)),
                partial=partial, seconds=round(time.time() - started, 1))
    try:
        (store.DATA / VALUE_META).write_text(json.dumps(meta), encoding='utf-8')   # beside the value index itself
    except OSError:
        pass
    return dict(columns=len(done), values=len(rows), skipped=skipped, partial=partial, seconds=meta['seconds'])


def complete_values():
    """Columns whose every distinct value is in the value index (set at the last refresh)."""
    try:
        return set(json.loads((store.DATA / VALUE_META).read_text(encoding='utf-8')).get('complete', []))
    except (OSError, ValueError):
        return set()


def finder(kb):
    local = None

    def find(phrase):
        n = norm(phrase)
        if not n:
            return []
        hits = [h for h in store.find_values(n) if kb.table(h['table'])]
        nonlocal local
        if local is None:
            local = {}
            try:
                with uploads_db() as conn:
                    for c, (_, _, _) in LOCAL_COLUMNS.items():
                        if kb.column(f'{LOCAL_TABLE}.{c}')['searchable']:
                            for (v,) in conn.execute(f'SELECT DISTINCT {c} FROM {LOCAL_TABLE} WHERE {c} IS NOT NULL'):
                                for part in str(v).split(', '):
                                    local.setdefault(norm(part), []).append(dict(table=LOCAL_TABLE, column=c, value=part, n=1))
            except Exception:
                local = {}
        if not hits and n in local:
            hits = local[n]
        return hits
    return find


def lookup_plan(kb, complete=None):
    """Live lookups in three tiers, cheapest first; a tier runs only when the ones before it found nothing.
    1 small tables: tolerant UPPER(TRIM(col)) = :v (cheap, forgives case and spaces)
    2 big tables, indexed columns: exact col = :v (an index probe)
    3 big tables, other columns: exact col = :v (a full scan, kept short by the time limit)
    Big-table columns whose values are all in the value index are skipped: a new code cannot hide there."""
    complete = complete_values() if complete is None else complete
    indexed = getattr(kb, 'indexed', None)
    tiers = ([], [], [])
    for t, tb in kb.tables.items():
        if tb.get('source') == 'local' or not IDENT.match(t):
            continue
        rows = tb.get('rows')
        small = rows is not None and rows <= SCAN_ROWS
        for c, m in tb['columns'].items():
            if not (m.get('searchable') and IDENT.match(c)) or (m['type'] not in TEXT and m['type'] != 'NUMBER'):
                continue
            if small:
                tiers[0].append((t, c, m))
            elif f'{t}.{c}' not in complete:
                tiers[1 if indexed is None or f'{t}.{c}' in indexed else 2].append((t, c, m))
    return tiers


def live_finder(kb, execute, dialect, budget_ms=LOOKUP_BUDGET_MS):
    """Exact lookups for codes not in the value index yet (e.g. an order saved a minute ago). Never a full scan of
    a big table with UPPER/TRIM (no index can help that); each big-table lookup has a short time limit and all
    lookups for one word share one budget, so an unknown word costs seconds at most, not minutes."""
    tiers = lookup_plan(kb)
    memo = {}

    def probe(t, c, m, token, tolerant, timeout_ms):
        if m['type'] == 'NUMBER':
            if not (token.isascii() and token.isdigit()) or len(token) > 30:
                return None
            where, binds = f'{c} = :v', {'v': int(token)}
        elif m.get('length') and len(token.encode()) > m['length']:
            return None   # longer than the column: cannot be stored there
        elif tolerant:
            where, binds = f'UPPER(TRIM({c})) = :v', {'v': token.upper()}
        else:   # as typed and in capitals: Oracle codes are usually stored in capitals
            values = list(dict.fromkeys([token, token.upper()]))
            pad = f', {int(m["length"])}' if m['type'] in ('CHAR', 'NCHAR') and m.get('length') else None
            binds = {f'v{i}': v for i, v in enumerate(values)}
            where = f'{c} IN ({", ".join(f"RPAD(:v{i}{pad})" if pad else f":v{i}" for i in range(len(values)))})'
        return limited(execute, timeout_ms)(dialect.limit(f'SELECT {c} FROM {dialect.table(t)} WHERE {where}', 1), binds, 1)

    def find(token):
        token = token.strip()
        if not token or len(token) > 120:
            return []
        if token in memo:
            return memo[token]
        hits = []; deadline = time.time() + budget_ms / 1000
        for tier, cols in enumerate(tiers):
            if hits:
                break
            for t, c, m in cols:
                left = (deadline - time.time()) * 1000
                if left < 100:
                    find.skipped += 1
                    continue
                try:   # small tables get the same limit: statistics can be stale
                    rows = probe(t, c, m, token, tier == 0, int(min(LOOKUP_MS, left)))
                except Exception:
                    continue
                if rows:
                    hits.append(dict(table=t, column=c, value=str(rows[0][0]).strip(), n=1))
        memo[token] = hits
        return hits
    find.skipped = 0
    return find
