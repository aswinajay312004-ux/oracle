"""Restricted, bind-only GARMENT_DEMO writer. Table/column identifiers come only from the
committed mapping registry and are re-checked against the live data dictionary."""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import json
import oracledb
from .config import OWNER, setting
from .erp import planner, mapping as registry, order_numbers
from .erp.catalog import OracleCatalog

digest = planner.digest
normalize = planner.normalize
count = planner.count


def _tag(conn, action):
    """Name the session in V$SESSION (MODULE / ACTION), so a DBA can see at once which statements come from
    POPPYS AI and from which part of it (idea from Oracle's SQLcl MCP server, docs/OPEN_SOURCE_RESEARCH.md)."""
    try:
        conn.module = 'POPPYS AI'; conn.action = action
    except Exception:   # an older client or a test double: naming the session is a help, never a reason to fail
        pass
    return conn


def connect():
    if not setting('ORACLE_PASSWORD'):
        raise RuntimeError('Local Oracle configuration is missing. Run scripts/bootstrap_local.py.')
    return _tag(oracledb.connect(user=setting('ORACLE_USER'), password=setting('ORACLE_PASSWORD'),
                                 dsn=setting('ORACLE_DSN', '127.0.0.1:1521/FREEPDB1'), tcp_connect_timeout=4), 'writer account')


READONLY_MISSING = ('The read-only Oracle account for Ask AI and the explorer is not set up. '
                    'Run python scripts\\create_readonly_user.py once on this computer.')
_VERIFIED_READONLY = set()


def write_privileges(conn):
    """Privileges of the connected account beyond reading (direct, through roles, or system-wide)."""
    c = conn.cursor()
    c.execute("""select privilege || ' on ' || table_name from user_tab_privs
                  where grantee = user and privilege not in ('SELECT', 'READ')
                 union all
                 select privilege || ' on ' || table_name from role_tab_privs
                  where privilege not in ('SELECT', 'READ')
                 union all
                 select privilege from session_privs
                  where privilege not in ('CREATE SESSION', 'SET CONTAINER', 'ALTER SESSION')""")
    return [r[0] for r in c.fetchmany(20)]


def connect_readonly():
    """The separate read-only account used by Ask AI, the explorer and the value index. The database itself
    refuses writes from it; this function also refuses to hand out a connection whose account can write."""
    user, password = setting('ORACLE_RO_USER'), setting('ORACLE_RO_PASSWORD')
    if not (user and password):
        raise RuntimeError(READONLY_MISSING)
    conn = oracledb.connect(user=user, password=password,
                            dsn=setting('ORACLE_DSN', '127.0.0.1:1521/FREEPDB1'), tcp_connect_timeout=4)
    if user.upper() not in _VERIFIED_READONLY:
        extra = write_privileges(conn)
        if extra:
            conn.close()
            raise RuntimeError(f'The account {user} is not read-only ({", ".join(extra[:5])}). '
                               'Ask AI and the explorer will not use it. Run scripts\\create_readonly_user.py.')
        _VERIFIED_READONLY.add(user.upper())
    return _tag(conn, 'read-only questions')


def current_mapping():
    from . import local_store
    return registry.effective(local_store.mapping_overrides())


def schema(conn, mapping=None):
    return OracleCatalog(conn).columns(registry.referenced_tables(mapping or current_mapping()))


def masters(conn, mapping=None, roles=None):
    """{role: [{id, label, cancelled}]} for the visible masters. With roles, only those masters, and one that
    cannot be listed (larger than the lookup limit, no grant) is left out instead of failing the others."""
    mapping = mapping or current_mapping(); cat = OracleCatalog(conn)
    visible = cat.columns(registry.referenced_tables(mapping))
    if roles is None:
        return {role: cat.masters(spec) for role, spec in mapping['masters'].items() if spec['table'] in visible}
    out = {}
    for role in roles:
        spec = mapping['masters'].get(role)
        if spec and spec['table'] in visible:
            try:
                out[role] = cat.masters(spec)
            except (ValueError, oracledb.Error):
                continue
    return out


def plan(conn, orders, filehash, mapping=None, required=()):
    return planner.plan(OracleCatalog(conn), orders, filehash, mapping or current_mapping(), required=required)


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


def receipt_extras(checked, draft):
    """What the import receipt says beyond the rows written: what did NOT reach Oracle and why, and the conversions
    applied. complete is True only when nothing from the file is left out (docs/TRANSACTION_AND_RECONCILIATION.md)."""
    not_saved = checked.get('not_saved') or []
    exclusions = list(((draft.get('coverage') or {}).get('exclusions')) or [])
    return dict(not_saved=not_saved, exclusions=exclusions, conversions=checked.get('conversions') or [],
                complete=not not_saved and not exclusions and not checked.get('reference_only'),
                filehash=draft.get('filehash'), plan_hash=checked.get('hash'))


def _write_timeout_ms(default_s=60, low=5, high=600):
    """WRITE_TIMEOUT_S from .env.poppys (seconds per statement of a save); anything that is not a number in range → default."""
    try:
        seconds = float(setting('WRITE_TIMEOUT_S', '') or default_s)
    except ValueError:
        seconds = default_s
    return int((seconds if low <= seconds <= high else default_s) * 1000)


WRITE_TIMEOUT_MS = _write_timeout_ms()


def _rollback(conn):
    """Roll back unless the driver has dropped the connection (then Oracle rolls back the session itself; touching the
    dropped connection can crash python-oracledb, see backend/insight/service.py usable())."""
    try:
        if conn.is_healthy():
            conn.rollback()
    except Exception:
        pass


def commit(draft, expected_hash, approver, mapping=None):
    mapping = mapping or current_mapping()
    with connect() as conn:
        conn.call_timeout = WRITE_TIMEOUT_MS        # a stuck lock or statement cannot hold the save (and the approver) forever
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
            assigned = assign_order_numbers(c, draft['orders'], mapping)
            given = [assigned.get(order_numbers.marker(i)) or str(o['order_ref']).strip() for i, o in enumerate(draft['orders'])]
            for ref in given:
                c.execute('INSERT INTO PP_ORDER_KEYS(ORDER_REF,JOBID) VALUES (:ref,:job)', ref=normalize(ref), job=draft['id'])
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
                    v = order_numbers.fill(v, assigned)
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
                    i = int(op['path'].split('.')[1])
                    outorders.append(dict(order_ref=given[i], ordmasid=ident, **({'automatic': True} if order_numbers.marker(i) in assigned else {})))
            result = dict(job_id=draft['id'], orders=outorders, rows=inserted, reused=reused, totals=checked['totals'],
                          reference_only=checked.get('reference_only', []),
                          committed_at=datetime.now().astimezone().isoformat(), approver=approver, already_committed=False,
                          **receipt_extras(checked, draft))
            c.setinputsizes(payload=oracledb.DB_TYPE_CLOB)
            c.execute('INSERT INTO PP_IMPORT_LOG(JOBID,FILEHASH,PLANHASH,APPROVER,RESULT_JSON) VALUES (:job,:filehash,:hash,:who,:payload)',
                      job=draft['id'], filehash=draft['filehash'], hash=expected_hash, who=approver,
                      payload=json.dumps(result, ensure_ascii=False))
            conn.commit(); return result
        except Exception:
            _rollback(conn); raise


def assign_order_numbers(cursor, orders, mapping):
    """{stand-in: OC number} for the orders saved without a reference. Called while PP_WRITE_LOCK is held, so the highest
    number read here cannot be taken by another POPPYS save before this transaction commits."""
    if all(str(o.get('order_ref') or '').strip() for o in orders):
        return {}
    spec = order_numbers.series()
    if spec is None:
        raise ValueError('An order has no OC number and no automatic series is set in config/company.json.')
    target = planner._written(mapping).get('order.order_ref')
    if not target or target[2] != 'verified':
        raise ValueError('The order reference has no verified column in the write mapping.')
    day = datetime.now().date()
    return order_numbers.assign(orders, _series_refs(cursor, spec, day, target), spec, day)


def _series_refs(cursor, spec, day, target):
    """The references of this year's series already in Oracle. Table and column come from the verified write mapping; the
    series prefix holds no LIKE wildcards (order_numbers.SAFE)."""
    table, column = target[0], target[1]
    cursor.execute(f'SELECT {column} FROM {OWNER}.{table} WHERE {column} LIKE :p', p=order_numbers.prefix(spec, day) + '%')
    return [r[0] for r in cursor]


def next_order_numbers(count, mapping=None):
    """The next `count` OC numbers as the screen shows them before saving (read only, no lock: the save itself takes the
    lock and may give a later number if another order was saved first). None when there is no automatic series."""
    spec = order_numbers.series()
    if spec is None:
        return None
    target = planner._written(mapping or current_mapping()).get('order.order_ref')
    if not target or target[2] != 'verified':
        return None
    day = datetime.now().date()
    with connect() as conn:
        return order_numbers.following(_series_refs(conn.cursor(), spec, day, target), order_numbers.prefix(spec, day), count, spec['digits'])


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
