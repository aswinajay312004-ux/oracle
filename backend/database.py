"""Database explorer, guided table creation and relationship designer.

Schema changes are never part of an order transaction: Oracle DDL commits implicitly.
Every statement is built here from validated structured input, shown to the admin,
and executed only through GARMENT_DEMO.PP_APPLY_DDL after an explicit confirmation."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import re
from .config import OWNER
from .oracle_store import IDENT, digest, fetch

NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,29}$')
RESERVED = {'ACCESS', 'ADD', 'ALL', 'ALTER', 'AND', 'ANY', 'AS', 'ASC', 'AUDIT', 'BETWEEN', 'BY', 'CHAR', 'CHECK', 'CLUSTER',
    'COLUMN', 'COMMENT', 'COMPRESS', 'CONNECT', 'CREATE', 'CURRENT', 'DATE', 'DECIMAL', 'DEFAULT', 'DELETE', 'DESC', 'DISTINCT',
    'DROP', 'ELSE', 'EXCLUSIVE', 'EXISTS', 'FILE', 'FLOAT', 'FOR', 'FROM', 'GRANT', 'GROUP', 'HAVING', 'IDENTIFIED', 'IMMEDIATE',
    'IN', 'INCREMENT', 'INDEX', 'INITIAL', 'INSERT', 'INTEGER', 'INTERSECT', 'INTO', 'IS', 'LEVEL', 'LIKE', 'LOCK', 'LONG',
    'MAXEXTENTS', 'MINUS', 'MODE', 'MODIFY', 'NOAUDIT', 'NOCOMPRESS', 'NOT', 'NOWAIT', 'NULL', 'NUMBER', 'OF', 'OFFLINE', 'ON',
    'ONLINE', 'OPTION', 'OR', 'ORDER', 'PCTFREE', 'PRIOR', 'PUBLIC', 'RAW', 'RENAME', 'RESOURCE', 'REVOKE', 'ROW', 'ROWID',
    'ROWNUM', 'ROWS', 'SELECT', 'SESSION', 'SET', 'SHARE', 'SIZE', 'SMALLINT', 'START', 'SUCCESSFUL', 'SYNONYM', 'SYSDATE',
    'TABLE', 'THEN', 'TO', 'TRIGGER', 'UID', 'UNION', 'UNIQUE', 'UPDATE', 'USER', 'VALIDATE', 'VALUES', 'VARCHAR', 'VARCHAR2',
    'VIEW', 'WHENEVER', 'WHERE', 'WITH'}
TYPES = ('VARCHAR2', 'NUMBER', 'DATE', 'CHAR', 'TIMESTAMP', 'CLOB')
# Suggested types for a non-technical admin; shown in the column editor.
TYPE_HELP = {'VARCHAR2': 'Text (up to a length)', 'NUMBER': 'Number', 'DATE': 'Date', 'CHAR': 'Fixed-length code',
             'TIMESTAMP': 'Date and time', 'CLOB': 'Long text'}


def check_name(value, what, errors):
    name = str(value or '').strip().upper()
    if not NAME.match(name):
        errors.append(f'{what} "{value}": use letters, numbers and _ (start with a letter, max 30).')
    elif name in RESERVED:
        errors.append(f'{what} "{name}" is an Oracle reserved word. Choose another name.')
    return name


def column_sql(col, errors):
    name = check_name(col.get('name'), 'Column', errors)
    kind = str(col.get('type') or '').upper()
    if kind not in TYPES:
        errors.append(f'{name}: choose a data type.')
        return name, ''
    if kind in ('VARCHAR2', 'CHAR'):
        limit = 4000 if kind == 'VARCHAR2' else 2000
        try:
            length = int(col.get('length') or (100 if kind == 'VARCHAR2' else 1))
            assert 1 <= length <= limit
        except (ValueError, AssertionError):
            errors.append(f'{name}: length must be 1–{limit}.'); length = 1
        sql = f'{kind}({length} CHAR)'
    elif kind == 'NUMBER':
        p, s = col.get('precision'), col.get('scale')
        if p in (None, ''):
            sql = 'NUMBER'
        else:
            try:
                p, s = int(p), int(s or 0)
                assert 1 <= p <= 38 and 0 <= s <= p
            except (ValueError, AssertionError):
                errors.append(f'{name}: digits must be 1–38 and decimals 0–digits.'); p, s = 38, 0
            sql = f'NUMBER({p},{s})'
    else:
        sql = kind
    default = col.get('default')
    if default not in (None, ''):
        d = str(default).strip()
        if kind == 'NUMBER':
            try:
                Decimal(d)
            except InvalidOperation:
                errors.append(f'{name}: default must be a number.')
            sql += f' DEFAULT {d}' if re.fullmatch(r'-?\d+(\.\d+)?', d) else ''
        elif kind in ('DATE', 'TIMESTAMP'):
            if d.upper() not in ('SYSDATE', 'SYSTIMESTAMP', 'TODAY', 'NOW'):
                errors.append(f'{name}: date default can only be "today".')
            sql += ' DEFAULT ' + ('SYSDATE' if kind == 'DATE' else 'SYSTIMESTAMP')
        elif kind in ('VARCHAR2', 'CHAR'):
            if re.search(r"[;\n\r]|--|/\*", d) or len(d) > 200:
                errors.append(f'{name}: default text cannot contain ; -- /* or line breaks.')
            sql += " DEFAULT '" + d.replace("'", "''") + "'"
        else:
            errors.append(f'{name}: default not supported for {kind}.')
    if col.get('required'):
        sql += ' NOT NULL'
    return name, sql


def table_ddl(spec, existing_tables):
    """Validated CREATE TABLE statement for the admin's structured table design."""
    errors = []
    table = check_name(spec.get('name'), 'Table', errors)
    if table in existing_tables:
        errors.append(f'A table named {table} already exists.')
    cols = spec.get('columns') or []
    if not 1 <= len(cols) <= 200:
        errors.append('Add between 1 and 200 columns.')
    parts, names = [], []
    for col in cols:
        name, sql = column_sql(col, errors)
        if name in names:
            errors.append(f'Column {name} is listed twice.')
        names.append(name)
        parts.append(f'"{name}" {sql}')
    pk = [check_name(c, 'Primary key column', errors) for c in spec.get('primary_key') or []]
    for c in pk:
        if c not in names:
            errors.append(f'Primary key column {c} is not in the table.')
        elif not next(x for x in cols if str(x.get('name')).strip().upper() == c).get('required'):
            errors.append(f'Primary key column {c} must be required.')
    if pk:
        parts.append(f'CONSTRAINT "{("PP_PK_" + table)[:30]}" PRIMARY KEY ({",".join(chr(34) + c + chr(34) for c in pk)})')
    for n, group in enumerate(spec.get('unique') or [], 1):
        group = [check_name(c, 'Unique column', errors) for c in group]
        if any(c not in names for c in group):
            errors.append('Unique columns must be in the table.')
        parts.append(f'CONSTRAINT "{("PP_UQ" + str(n) + "_" + table)[:30]}" UNIQUE ({",".join(chr(34) + c + chr(34) for c in group)})')
    if not pk:
        errors.append('Choose a primary key column so each record can be identified (for example an ID number column).')
    ddl = f'CREATE TABLE "{OWNER}"."{table}" (' + ', '.join(parts) + ')'
    result = dict(table=table, ddl=ddl, errors=list(dict.fromkeys(errors)))
    result['hash'] = digest(result)
    return result


def explorer(conn, registry, keys):
    c = conn.cursor()
    c.execute('select table_name, num_rows from all_tables where owner=:o order by table_name', o=OWNER)
    stats = dict(c.fetchall())
    return [dict(name=t, columns=len(cols), rows_estimate=stats.get(t), primary_key=[k['column'] for k in keys.get(t, []) if k['type'] == 'primary'])
            for t, cols in sorted(registry.items()) if t in stats]


def table_detail(conn, registry, keys, relationships, table, limit=50, offset=0):
    if table not in registry or not IDENT.match(table):
        raise KeyError('Table not found')
    c = conn.cursor()
    c.execute(f'SELECT count(*) FROM {OWNER}."{table}"')
    total = c.fetchone()[0]
    pk = next((k['column'] for k in keys.get(table, []) if k['type'] == 'primary'), None)
    order = f' ORDER BY "{pk}"' if pk else ''
    c.execute(f'SELECT * FROM {OWNER}."{table}"{order} OFFSET {int(offset)} ROWS FETCH NEXT {int(min(limit, 200))} ROWS ONLY')
    names = [d[0] for d in c.description]
    from .oracle_store import plain
    rows = [dict(zip(names, map(plain, r))) for r in c.fetchall()]
    return dict(name=table, total=total, rows=rows, offset=offset,
                columns=[dict(name=k, **v) for k, v in registry[table].items()], keys=keys.get(table, []),
                relationships=[r for r in relationships if table in (r['parent'], r['child'])])


def compatible(parent, child):
    p, c = parent['type'], child['type']
    if p != c and not ({p, c} <= {'VARCHAR2', 'CHAR'}):
        return f'{c} cannot point to {p}.'
    if p in ('VARCHAR2', 'CHAR') and child['length'] > parent['length']:
        return f'child column is longer ({child["length"]}) than the parent ({parent["length"]}).'
    if p == 'NUMBER' and (child.get('scale') or 0) > (parent.get('scale') or 0):
        return 'child column allows decimals the parent does not.'
    return None


def has_cycle(edges, new):
    graph = {}
    for e in edges + [new]:
        if e['parent'] != e['child']:
            graph.setdefault(e['parent'], set()).add(e['child'])
    seen, stack = set(), set()

    def visit(n):
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n); stack.add(n)
        found = any(visit(x) for x in graph.get(n, ()))
        stack.discard(n)
        return found
    return any(visit(n) for n in list(graph))


def validate_relationship(conn, registry, keys, relationships, spec):
    """Checks before a relationship is saved: types, parent key, existing data and cycles."""
    errors, warnings = [], []
    p, pc, ch, cc = (str(spec.get(k) or '').upper() for k in ('parent', 'parent_column', 'child', 'child_column'))
    kind = spec.get('kind') if spec.get('kind') in ('application', 'physical') else 'application'
    for t, col in ((p, pc), (ch, cc)):
        if t not in registry or col not in registry.get(t, {}):
            errors.append(f'{t}.{col} does not exist in Oracle.')
    if errors:
        return dict(errors=errors, warnings=warnings, kind=kind)
    if any(r['parent'] == p and r['parent_column'] == pc and r['child'] == ch and r['child_column'] == cc for r in relationships):
        errors.append('This relationship already exists.')
    reason = compatible(registry[p][pc], registry[ch][cc])
    if reason:
        errors.append('Data types do not match: ' + reason)
    unique = any(k['column'] == pc and len([x for x in keys.get(p, []) if x['name'] == k['name']]) == 1 for k in keys.get(p, []))
    c = conn.cursor()
    c.execute(f'SELECT count("{pc}"), count(DISTINCT "{pc}") FROM {OWNER}."{p}"')
    filled, distinct = c.fetchone()
    if not unique:
        if kind == 'physical':
            errors.append(f'{p}.{pc} is not a primary or unique key, so Oracle cannot enforce a foreign key to it.')
        elif filled != distinct:
            errors.append(f'{p}.{pc} has repeated values, so a child record could point to more than one parent.')
        else:
            warnings.append(f'{p}.{pc} is unique today but not protected by a key constraint.')
    c.execute(f'''SELECT count(*) FROM {OWNER}."{ch}" x WHERE x."{cc}" IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM {OWNER}."{p}" y WHERE y."{pc}" = x."{cc}")''')
    orphans = c.fetchone()[0]
    if orphans:
        (errors if kind == 'physical' else warnings).append(f'{orphans} existing {ch} records point to a {p} value that does not exist.')
    if registry[ch][cc]['nullable'] == 'Y':
        warnings.append(f'{ch}.{cc} is optional, so a {ch} record may have no parent (one-to-many, optional).')
    if has_cycle(relationships, dict(parent=p, child=ch)):
        errors.append('This link would create a loop between tables, which makes the save order impossible.')
    result = dict(parent=p, parent_column=pc, child=ch, child_column=cc, kind=kind, errors=errors, warnings=warnings,
                  cardinality='one-to-one' if any(k['column'] == cc for k in keys.get(ch, [])) else 'one-to-many')
    if kind == 'physical':
        name = ('PP_FK_' + ch + '_' + cc)[:30]
        result['ddl'] = f'ALTER TABLE "{OWNER}"."{ch}" ADD CONSTRAINT "{name}" FOREIGN KEY ("{cc}") REFERENCES "{OWNER}"."{p}" ("{pc}")'
    result['hash'] = digest(result)
    return result


def ddl_enabled(conn):
    c = conn.cursor()
    c.execute("select count(*) from all_procedures where owner=:o and object_name='PP_APPLY_DDL'", o=OWNER)
    return c.fetchone()[0] > 0


def apply_ddl(conn, kind, table, ddl, approver):
    if not ddl_enabled(conn):
        raise ValueError('Schema changes are not enabled on this Oracle. Run scripts/upgrade_local.py as the database administrator.')
    c = conn.cursor()
    c.callproc(f'{OWNER}.PP_APPLY_DDL', [kind, table, ddl])
    c.execute('INSERT INTO PP_SCHEMA_LOG(ACTION,OBJECT_NAME,DDL,APPROVER) VALUES (:a,:o,:d,:w)', a=kind, o=table, d=ddl, w=approver)
    conn.commit()


def schema_log(conn):
    c = conn.cursor()
    c.execute('SELECT ACTION,OBJECT_NAME,DDL,APPROVER,EXECUTED_AT FROM PP_SCHEMA_LOG ORDER BY EXECUTED_AT DESC FETCH FIRST 50 ROWS ONLY')
    return [dict(action=a, object=o, ddl=d.read() if hasattr(d, 'read') else d, approver=w, at=t.isoformat()) for a, o, d, w, t in c]
