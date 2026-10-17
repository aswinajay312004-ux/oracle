"""I/O for the data intelligence layer: dictionary harvesting (bounded, statistics instead of COUNT(*)),
the knowledge cache, the value index, read-only executors and the local 'uploaded files' source."""
from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timezone
from .. import local_store as store
from ..config import DATA, OWNER, ROOT
from .knowledge import build, Knowledge, norm, IDENT
from .query import Dialect

SNAPSHOT = ROOT / 'docs' / 'live_schema_columns.json'
CACHE = 'knowledge_physical.json'
VALUE_CAP = 20000          # distinct values indexed per column
BIG_TABLE = 2_000_000      # transactional columns of bigger tables are looked up live instead of indexed
TIMEOUT_MS = 20000
_memo = {}; _lock = threading.RLock()

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
    return dict(physical=physical, stats=stats, comments=comments, fks=list(fks.values()), live=True,
                harvested_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def snapshot():
    physical = {}
    for r in json.loads(SNAPSHOT.read_text(encoding='utf-8')):
        physical.setdefault(r['table'], {})[r['column']] = dict(type=r['type'], length=r['length'], precision=r['precision'],
                                                                 scale=r['scale'], nullable=r['nullable'])
    return dict(physical=physical, stats={}, comments={}, fks=[], live=False, harvested_at=None)


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
    return Knowledge(data)


def invalidate():
    with _lock:
        _memo.clear()


# --- executors ------------------------------------------------------------------------------------
def _guard(sql):
    s = sql.lstrip().upper()
    if not s.startswith('SELECT') or ';' in sql:
        raise ValueError('Only single SELECT statements may run.')


class OracleExecutor:
    """Read-only execution: SET TRANSACTION READ ONLY, call timeout, bounded fetch, always rolled back."""

    def __init__(self, conn, timeout_ms=TIMEOUT_MS):
        self.conn = conn; conn.call_timeout = timeout_ms

    def __call__(self, sql, binds, max_rows):
        _guard(sql)
        c = self.conn.cursor()
        try:
            c.execute('SET TRANSACTION READ ONLY')
            c.arraysize = max(50, min(int(max_rows), 1000))
            c.execute(sql, binds)
            return c.fetchmany(int(max_rows))
        finally:
            self.conn.rollback()


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
    for name, t in kb.tables.items():
        if t.get('source') == 'local':
            continue
        big = (t.get('rows') or 0) > BIG_TABLE
        for c, m in t['columns'].items():
            if m.get('searchable') and m['type'] in ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'VARCHAR') and IDENT.match(c):
                if big and t['kind'] != 'master':
                    continue
                yield name, c


def refresh_values(kb, execute, dialect):
    rows = []; done = []; skipped = []
    for t, c in indexable(kb):
        sql = dialect.limit(f'SELECT {c}, COUNT(*) FROM {dialect.table(t)} WHERE {c} IS NOT NULL GROUP BY {c}', VALUE_CAP)
        try:
            for value, n in execute(sql, {}, VALUE_CAP):
                v = str(value).strip()
                if v and len(v) <= 120:
                    rows.append((t, c, v, norm(v), int(n)))
            done.append(f'{t}.{c}')
        except Exception as exc:
            skipped.append(f'{t}.{c}: {str(exc)[:80]}')
    store.replace_values(rows)
    return dict(columns=len(done), values=len(rows), skipped=skipped)


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


def live_finder(kb, execute, dialect):
    """Exact lookups for codes not in the value index yet (e.g. an order saved a minute ago)."""
    cols = [(t, c) for t, c in indexable(kb)] + [(t, c) for t, tb in kb.tables.items() for c, m in tb['columns'].items()
                                                  if m.get('searchable') and (tb.get('rows') or 0) > BIG_TABLE and tb.get('source') != 'local']

    def find(token):
        hits = []
        for t, c in dict.fromkeys(cols):
            try:
                rows = execute(dialect.limit(f'SELECT {c} FROM {dialect.table(t)} WHERE UPPER(TRIM({c})) = :v', 1), {'v': token.strip().upper()}, 1)
            except Exception:
                continue
            if rows:
                hits.append(dict(table=t, column=c, value=str(rows[0][0]).strip(), n=1))
        return hits
    return find
