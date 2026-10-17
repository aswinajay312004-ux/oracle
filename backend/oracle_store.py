"""Restricted, bind-only GARMENT_DEMO writer. Table/column identifiers come only from the
committed mapping registry and are re-checked against the live data dictionary."""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import json
import oracledb
from .config import OWNER, setting
from .erp import planner, mapping as registry
from .erp.catalog import OracleCatalog

digest = planner.digest
normalize = planner.normalize
count = planner.count


def connect():
    if not setting('ORACLE_PASSWORD'):
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    return oracledb.connect(user=setting('ORACLE_USER'), password=setting('ORACLE_PASSWORD'),
                            dsn=setting('ORACLE_DSN', '127.0.0.1:1521/FREEPDB1'), tcp_connect_timeout=4)


def current_mapping():
    from . import local_store
    return registry.effective(local_store.mapping_overrides())


def schema(conn, mapping=None):
    return OracleCatalog(conn).columns(registry.referenced_tables(mapping or current_mapping()))


def masters(conn, mapping=None):
    mapping = mapping or current_mapping(); cat = OracleCatalog(conn)
    visible = cat.columns(registry.referenced_tables(mapping))
    return {role: cat.masters(spec) for role, spec in mapping['masters'].items() if spec['table'] in visible}


def plan(conn, orders, filehash, mapping=None):
    return planner.plan(OracleCatalog(conn), orders, filehash, mapping or current_mapping())


def read_result(cursor, jobid):
    cursor.execute('SELECT RESULT_JSON FROM PP_IMPORT_LOG WHERE JOBID=:id', id=jobid)
    row = cursor.fetchone()
    if not row:
        return None
    result = json.loads(row[0].read() if hasattr(row[0], 'read') else row[0]); result['already_committed'] = True
    return result


def pk_of(table, mapping):
    if table in mapping['tables']:
        return mapping['tables'][table]['key']
    for m in mapping['masters'].values():
        if m['table'] == table:
            return m['key']
    raise ValueError(f'{table}: not an allowlisted table.')


def same(a, b):
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        return a == b
    if a is None or b is None:
        return a is b
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()[:10] if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if hasattr(v, 'read'):
        v = v.read()
    if isinstance(v, (bytes, bytearray)):
        return f'<binary, {len(v):,} bytes>'
    return v


def commit(draft, expected_hash, approver, mapping=None):
    mapping = mapping or current_mapping()
    with connect() as conn:
        c = conn.cursor(); c.execute('SELECT ID FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10')
        previous = read_result(c, draft['id'])
        if previous:
            return previous
        checked = plan(conn, draft['orders'], draft['filehash'], mapping)
        if checked['errors']:
            raise ValueError('; '.join(checked['errors']))
        if checked['hash'] != expected_hash:
            raise ValueError('Database or preview changed. Generate a fresh preview and review it before accepting.')
        registry_cols = schema(conn, mapping)
        refs = {}; inserted = []; reused = []; outorders = []
        try:
            for o in draft['orders']:
                c.execute('INSERT INTO PP_ORDER_KEYS(ORDER_REF,JOBID) VALUES (:ref,:job)', ref=normalize(o['order_ref']), job=draft['id'])
            for op in checked['operations']:
                table = op['table']; pk = pk_of(table, mapping)
                if op['action'] == 'reuse':
                    reused.append(dict(table=table, key=op['key'], pk=op['values'][pk], business=op.get('business'), values=op['values']))
                    continue
                c.execute('SELECT PP_RECORD_SEQ.nextval FROM dual'); ident = c.fetchone()[0]
                refs[op['key']] = ident
                vals = {}
                for col, v in op['values'].items():
                    if isinstance(v, str) and v.startswith('$'):
                        v = refs[v[1:]]
                    if v is not None and registry_cols[table][col]['type'] == 'DATE':
                        v = datetime.strptime(v, '%Y-%m-%d')
                    elif isinstance(v, float):
                        v = Decimal(str(v))
                    vals[col] = v
                cols = list(vals); binds = {f'v{i}': vals[col] for i, col in enumerate(cols)}
                c.execute(f'INSERT INTO {OWNER}.{table} ({",".join(cols)}) VALUES ({",".join(":" + k for k in binds)})', binds)
                c.execute(f'SELECT {",".join(cols)} FROM {OWNER}.{table} WHERE {pk}=:id', id=ident)
                rows = c.fetchall()
                if len(rows) != 1 or not all(same(a, b) for a, b in zip(rows[0], vals.values())):
                    raise ValueError(f'{table}: inserted values did not match read-back.')
                inserted.append(dict(table=table, key=op['key'], pk=ident, business=op.get('business'), where=op.get('where'),
                                     values={col: jsonable(v) for col, v in zip(cols, rows[0])}))
                if table == 'ORDMAS':
                    outorders.append(dict(order_ref=vals['DOCID'], ordmasid=ident))
            result = dict(job_id=draft['id'], orders=outorders, rows=inserted, reused=reused, totals=checked['totals'],
                          reference_only=checked.get('reference_only', []),
                          committed_at=datetime.now().astimezone().isoformat(), approver=approver, already_committed=False)
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                      job=draft['id'], filehash=draft['filehash'], hash=expected_hash, who=approver,
                      payload=json.dumps(result, ensure_ascii=False))
            conn.commit(); return result
        except Exception:
            conn.rollback(); raise


def history():
    with connect() as conn:
        c = conn.cursor(); c.execute('SELECT JOBID,PLANHASH,APPROVER,COMMITTED_AT FROM PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC FETCH FIRST 100 ROWS ONLY')
        return [dict(job_id=j, plan_hash=h, approver=a, committed_at=d.isoformat()) for j, h, a, d in c]


def live_rows(conn, table, pk, ids, mapping):
    """Read complete current rows by primary key. Table and key are allowlisted by the registry."""
    if not ids:
        return {}
    if pk != pk_of(table, mapping):
        raise ValueError('Primary key mismatch.')
    c = conn.cursor(); out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        binds = {f'i{n}': v for n, v in enumerate(chunk)}
        c.execute(f'SELECT * FROM {OWNER}.{table} WHERE {pk} IN ({",".join(":" + k for k in binds)})', binds)
        names = [d[0] for d in c.description]
        for row in c:
            record = {n: jsonable(v) for n, v in zip(names, row)}
            out[record[pk]] = record
    return out
