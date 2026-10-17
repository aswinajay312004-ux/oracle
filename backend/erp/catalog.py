"""What the planner may know about GARMENT_DEMO: columns, privileges, masters and existing keys.

OracleCatalog reads the live data dictionary through the restricted POPPYS_APP connection.
SnapshotCatalog reads the committed dictionary export for offline previews and tests; it can
never authorise a save (``live`` is False and the API refuses to commit with it).
"""
from __future__ import annotations
import json
from ..config import OWNER, ROOT

SNAPSHOT = ROOT / 'docs' / 'live_schema_columns.json'
MASTER_LIMIT = 5000


def _column(kind, length, precision, scale, nullable):
    return dict(type=kind, length=length, precision=precision, scale=scale, nullable=nullable)


class Catalog:
    live = False

    def columns(self, tables):
        """{table: {column: meta}} for tables visible to the application."""
        raise NotImplementedError

    def insertable(self, tables):
        raise NotImplementedError

    def masters(self, spec):
        """[{'id','label','cancelled'}] for one master definition."""
        raise NotImplementedError

    def file_committed(self, filehash):
        raise NotImplementedError

    def order_ref_exists(self, ref):
        raise NotImplementedError


class OracleCatalog(Catalog):
    live = True

    def __init__(self, conn):
        self.conn = conn
        self._cols = None

    def columns(self, tables):
        if self._cols is None:
            c = self.conn.cursor()
            c.execute('''select table_name,column_name,data_type,data_length,data_precision,data_scale,nullable
                from all_tab_columns where owner=:owner order by table_name,column_id''', owner=OWNER)
            self._cols = {}
            for table, col, *meta in c:
                self._cols.setdefault(table, {})[col] = _column(*meta)
        return {t: self._cols[t] for t in tables if t in self._cols}

    def visible_tables(self):
        self.columns([])
        return sorted(self._cols)

    def insertable(self, tables):
        c = self.conn.cursor()
        c.execute("select table_name from user_tab_privs where owner=:owner and privilege='INSERT'", owner=OWNER)
        granted = {r[0] for r in c}
        return {t for t in tables if t in granted}

    def masters(self, spec):
        table, key, label = spec['table'], spec['key'], spec['label']
        cols = self.columns([table]).get(table, {})
        cancel = 'CANCEL' if 'CANCEL' in cols else 'NULL'
        c = self.conn.cursor()
        # Identifiers come only from the committed mapping registry and are checked against the dictionary.
        c.execute(f'SELECT {key},{label},{cancel} FROM {OWNER}.{table} ORDER BY {key} FETCH FIRST {MASTER_LIMIT + 1} ROWS ONLY')
        rows = c.fetchall()
        if len(rows) > MASTER_LIMIT:
            raise ValueError(f'{table}: master catalogue exceeds {MASTER_LIMIT} rows; configure a scoped lookup.')
        return [dict(id=k, label=str(v).strip(), cancelled=str(x or '').upper() == 'T') for k, v, x in rows if v is not None]

    def file_committed(self, filehash):
        c = self.conn.cursor()
        c.execute('select JOBID from PP_IMPORT_LOG where FILEHASH=:h fetch first 1 rows only', h=filehash)
        return c.fetchone() is not None

    def order_ref_exists(self, ref):
        c = self.conn.cursor()
        c.execute(f'SELECT ORDMASID FROM {OWNER}.ORDMAS WHERE upper(trim(DOCID))=:ref FETCH FIRST 1 ROWS ONLY', ref=ref)
        return c.fetchone() is not None


class SnapshotCatalog(Catalog):
    """Dictionary export + optional in-memory masters. Read-only, never live."""

    def __init__(self, masters=None, existing_refs=(), committed_hashes=(), insertable=None, path=SNAPSHOT):
        self._cols = {}
        for r in json.loads(path.read_text(encoding='utf-8')):
            self._cols.setdefault(r['table'], {})[r['column']] = _column(
                r['type'], r['length'], r['precision'], r['scale'], r['nullable'])
        self._masters = masters or {}
        self._refs = {str(r).strip().upper() for r in existing_refs}
        self._hashes = set(committed_hashes)
        self._insertable = insertable

    def columns(self, tables):
        return {t: self._cols[t] for t in tables if t in self._cols}

    def insertable(self, tables):
        if self._insertable is None:
            return {t for t in tables if t in self._cols}
        return {t for t in tables if t in self._insertable}

    def masters(self, spec):
        return [dict(x) for x in self._masters.get(spec['table'], [])]

    def file_committed(self, filehash):
        return filehash in self._hashes

    def order_ref_exists(self, ref):
        return ref in self._refs
