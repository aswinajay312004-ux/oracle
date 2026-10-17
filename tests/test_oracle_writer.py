"""The Oracle writer (backend/oracle_store.commit) and read-back, without a database: generated-ID references are
never text, blank text is planned as the NULL Oracle keeps, the read-back compares by column type, the save lock row
must exist, and saves go only to an allowed database. The same paths run on the replica in tests/test_oracle.py."""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import secrets
import pytest
from backend import oracle_store, workbooks
from backend.erp import planner, mapping as registry, readback
from backend.erp.catalog import SnapshotCatalog
from fake_oracle import LiveSnapshot

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def order(**kw):
    o = workbooks.base_order()
    o.update(buyer='DEMO BUYER', style='ST-1', description='TEST BODYSUIT', po_number='PO-1', sample_type='DEVELOPMENT SAMPLE',
             order_ref='T-' + secrets.token_hex(4).upper(), order_date='2026-10-17', ship_date='2026-10-24',
             qty_unit='PCS', pieces_per_unit=1, quantity_basis='sample',
             lines=[dict(label='L', color='BLUE', declared_total=10, sizes=[dict(size='M', quantity=4), dict(size='L', quantity=6)])])
    o.update(kw)
    return o


def plan(o, m=None):
    return planner.plan(SnapshotCatalog(), [o], 'f' * 64, m or registry.effective())


class Writer:
    """Stands in for one Oracle session: answers the writer's queries and keeps what an INSERT stored ('' as NULL)."""

    def __init__(self, identity=('FREEPDB1', 'FREE'), lock_row=True):
        self.identity = identity; self.lock_row = lock_row; self.sql = []; self.inserted = []; self.seq = 5000; self.committed = False

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def commit(self): self.committed = True
    def rollback(self): pass

    def cursor(self):
        conn = self

        class Cursor:
            res = []; last = None

            def execute(self, sql, binds=None, **kw):
                conn.sql.append(sql); s = ' '.join(sql.split()).upper()
                if 'SYS_CONTEXT' in s:
                    self.res = [conn.identity]
                elif 'PP_WRITE_LOCK' in s:
                    self.res = [(1,)] if conn.lock_row else []
                elif 'PP_RECORD_SEQ' in s:
                    conn.seq += 1; self.res = [(conn.seq,)]
                elif s.startswith('INSERT INTO GARMENT_DEMO.'):
                    conn.inserted.append((s.split('GARMENT_DEMO.')[1].split()[0], list(binds.values())))
                    self.last = [None if v == '' else v for v in binds.values()]      # Oracle keeps '' as NULL
                elif s.startswith('SELECT') and 'FROM GARMENT_DEMO.' in s:
                    self.res = [tuple(self.last)]
                else:
                    self.res = []

            def fetchone(self): return self.res[0] if self.res else None
            def fetchall(self): return list(self.res)
            def setinputsizes(self, **kw): pass
        return Cursor()


@pytest.fixture
def writer(monkeypatch):
    """The real commit() against a Writer, planned with the dictionary snapshot; the local database by default."""
    for key, value in (('ORACLE_DSN', '127.0.0.1:1521/FREEPDB1'), ('ORACLE_ALLOWED_HOSTS', ''), ('ORACLE_DB_IDENTITY', '')):
        monkeypatch.setenv('POPPYS_' + key, value)
    conn = Writer()
    monkeypatch.setattr(oracle_store, 'connect', lambda *a, **k: conn)
    monkeypatch.setattr(oracle_store, 'read_result', lambda cursor, jobid: None)
    monkeypatch.setattr(oracle_store, 'schema', lambda c, mapping=None: SnapshotCatalog().columns(registry.referenced_tables(mapping)))
    monkeypatch.setattr(oracle_store, 'plan', lambda c, orders, filehash, mapping=None, required=():
                        planner.plan(LiveSnapshot(), orders, filehash, mapping or registry.effective(), required=required))

    def save(o, m=None):
        d = dict(id=secrets.token_hex(8), filehash=secrets.token_hex(32), orders=[o])
        p = oracle_store.plan(None, d['orders'], d['filehash'], m)
        assert not p['errors'], p['errors']
        return oracle_store.commit(d, p['hash'], 'tester', mapping=m)
    conn.save = save
    return conn


# --- finding 2: '$' text is text; references are planner.Ref ----------------------------------------------------
def test_text_starting_with_a_dollar_is_saved_as_typed(writer):
    receipt = writer.save(order(po_number='$o1', description='$5 TEE'))
    head = next(r for r in receipt['rows'] if r['table'] == 'ORDMAS')
    assert head['values']['BONO'] == '$o1' and head['values']['ORDMASID'] == head['pk'] != '$o1'
    assert next(r for r in receipt['rows'] if r['table'] == 'STYLEMAS')['values']['SDESC'] == '$5 TEE'
    line = next(r for r in receipt['rows'] if r['table'] == 'ORDSTYLE')
    assert line['values']['PONUM'] == '$o1' and line['values']['ORDMASID'] == head['pk']   # the real reference resolved


def test_generated_ids_are_references_that_survive_json_and_hash_the_same():
    p = plan(order(po_number='$o1'))
    head = next(op for op in p['operations'] if op['table'] == 'ORDMAS')
    assert isinstance(head['values']['ORDMASID'], planner.Ref) and head['values']['ORDMASID'] == {'ref': 'o1'}
    assert head['values']['BONO'] == '$o1' and not isinstance(head['values']['BONO'], planner.Ref)
    stored = json.loads(json.dumps(p))                               # draft storage / API response
    assert next(op for op in stored['operations'] if op['table'] == 'ORDMAS')['values']['ORDMASID'] == {'ref': 'o1'}
    o = order(); o['order_ref'] = 'SAME'
    assert plan(o)['hash'] == plan(json.loads(json.dumps(o)))['hash']    # deterministic
    graph = readback.planned(p, registry.effective())                  # approval screen: masters decoded through refs
    line = next(n for n in graph['nodes'] if n['table'] == 'ORDSTYLE')['records'][0]
    assert line['decoded']['COMBO'] == 'BLUE'


def test_a_value_that_is_not_plain_text_is_refused_not_saved():
    p = plan(order(po_number={'ref': 'o1'}))
    assert any('Buyer PO / contract number must be plain text or a number' in e for e in p['errors'])
    assert 'BONO' not in next(op for op in p['operations'] if op['table'] == 'ORDMAS')['values']


def test_dollar_text_gets_the_length_and_formula_checks():
    p = plan(order(po_number='$' + 'X' * 80))
    assert any('Buyer PO / contract number can hold at most 55 characters' in e and '(Oracle ORDMAS.BONO)' in e for e in p['errors'])
    p = plan(order(po_number="$cmd|' /C calc'!A0"))
    assert any('spreadsheet command' in e for e in p['errors'])


def test_a_reference_to_a_row_not_saved_is_a_plain_error(writer, monkeypatch):
    real = oracle_store.plan

    def broken(*a, **k):
        p = real(*a, **k); p['operations'][-1]['values']['ORDSIZEID'] = planner.Ref('missing'); return p
    monkeypatch.setattr(oracle_store, 'plan', broken)
    with pytest.raises(RuntimeError, match='not saved yet'):
        writer.save(order())
    assert not writer.committed


# --- finding 6: blank text ------------------------------------------------------------------------------------
def test_blank_text_is_planned_as_null_and_saves():
    from test_planner import order_from_fixture, verified_bom
    o = order_from_fixture(accessories=[])
    for f in o['fabrics']:
        f['dia'] = ''
    p = planner.plan(SnapshotCatalog(), [o], 'f' * 64, verified_bom())
    assert [op['values']['DIA'] for op in p['operations'] if op['table'] == 'ORDFAB'] == [None, None, None]


def test_blank_text_saves_through_the_writer(writer):
    m = registry.effective([dict(table='ORDMAS', column='OAGAINST', status='verified')])
    o = order(against='')
    m['tables']['ORDMAS']['columns']['OAGAINST'].pop('optional')           # a required text column left blank
    receipt = writer.save(o, m)
    assert next(r for r in receipt['rows'] if r['table'] == 'ORDMAS')['values']['OAGAINST'] is None


def test_blank_is_missing_for_a_not_null_column():
    issues = planner.Issues()
    planner._check_columns([dict(action='insert', table='T', values={'A': '', 'B': 'x'}, where='Order 1', path=None)],
                           {'T': {'A': dict(type='VARCHAR2', length=5, precision=None, scale=None, nullable='N'),
                                  'B': dict(type='VARCHAR2', length=5, precision=None, scale=None, nullable='N')}}, issues, {})
    assert issues.messages('error') == ['T.A: mandatory database value missing.']


# --- findings 7 and 9: the read-back compares by the column's type --------------------------------------------
@pytest.mark.parametrize('a,b,kind,equal', [
    ('NAN123', 'NAN123', 'VARCHAR2', True), ('nan', 'nan', None, True), ('0042', '42', 'VARCHAR2', False),
    ('0042', '42', None, False), ('1E3', '1000', None, False), ('VG84392881 CANCELLED', 'VG84392881', None, False),
    ('PO-0000123-B', 'PO-0000123', 'VARCHAR2', False), (42, Decimal('42.0'), 'NUMBER', True), (6.18, Decimal('6.18'), 'NUMBER', True),
    (42, 43, None, False), ('AB   ', 'AB', 'CHAR', True), ('AB ', 'AB', 'VARCHAR2', False), (5, '5', 'VARCHAR2', True),
    (datetime(2026, 10, 17), '2026-10-17', 'DATE', True), (datetime(2026, 10, 17, 9), '2026-10-17', 'DATE', False),
    ('2026-10-17', '2026-10-17', 'TIMESTAMP(6)', True), (None, '', 'VARCHAR2', False), (None, None, 'NUMBER', True),
])
def test_same_compares_by_column_type(a, b, kind, equal):
    assert readback.same(a, b, kind) is equal
    assert oracle_store.same is readback.same


def test_history_graph_reports_changed_rows():
    m = registry.effective()
    receipt = dict(job_id='j', rows=[dict(table='ORDMAS', pk=1, values=dict(ORDMASID=1, BONO='VG84392881', DOCID='0042'))], reused=[])
    live = {1: dict(ORDMASID=1, BONO='VG84392881 CANCELLED', DOCID='42')}
    g = readback.graph(receipt, m, lambda t, pk, ids: live)
    assert g['verified'] is False and g['nodes'][0]['records'][0]['mismatched'] == ['BONO', 'DOCID']
    typed = dict(receipt, column_types={'ORDMAS': dict(ORDMASID='NUMBER', BONO='CHAR', DOCID='VARCHAR2')})
    g = readback.graph(typed, m, lambda t, pk, ids: {1: dict(ORDMASID=1.0, BONO='VG84392881   ', DOCID='0042')})
    assert g['verified'] is True                    # CHAR blank padding and 1 == 1.0 are the same value


def test_receipt_records_the_column_types(writer):
    receipt = writer.save(order())
    assert receipt['column_types']['ORDMAS']['BONO'] == 'VARCHAR2' and receipt['column_types']['ORDMAS']['ODATE'] == 'DATE'
    assert set(receipt['column_types']) == {r['table'] for r in receipt['rows'] + receipt['reused']}


# --- finding 14: the save lock row ------------------------------------------------------------------------------
def test_a_missing_save_lock_row_refuses_the_save(writer):
    writer.lock_row = False
    with pytest.raises(RuntimeError, match='save lock'):
        writer.save(order())
    assert not writer.inserted and not writer.committed


# --- finding 11: saves go only to an allowed database -------------------------------------------------------------
@pytest.mark.parametrize('dsn,hosts', [
    ('127.0.0.1:1521/FREEPDB1', ['127.0.0.1']), ('localhost/FREEPDB1', ['localhost']), ('//localhost:1521/X', ['localhost']),
    ('tcps://db.example.com:2484/X?wallet_location=/w', ['db.example.com']), ('[::1]:1521/FREEPDB1', ['::1']),
    ('h1,h2:1521/X', ['h1', 'h2']), ('MYALIAS', []), ('', []),
    ('(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)(HOST=127.0.0.1)(PORT=1521))(ADDRESS=(PROTOCOL=TCP)(host = prod.example)(PORT=1521)))'
     '(CONNECT_DATA=(SERVICE_NAME=X)))', ['127.0.0.1', 'prod.example']),
])
def test_dsn_hosts(dsn, hosts):
    assert oracle_store.dsn_hosts(dsn) == hosts


@pytest.mark.parametrize('dsn,allowed,ok', [
    ('127.0.0.1:1521/FREEPDB1', '', True), ('localhost:1521/FREEPDB1', '', True), ('[::1]:1521/FREEPDB1', '', True),
    ('127.0.0.2:1521/FREEPDB1', '', True), ('192.0.2.10:1521/CLIENTPROD', '', False), ('MYALIAS', '', False),
    ('h1,127.0.0.1:1521/X', '', False), ('dbtest.local:1521/X', 'dbtest.local', True),
    ('127.0.0.1:1521/X', 'dbtest.local', False), ('DBTEST.local:1521/X', 'dbtest.local, 127.0.0.1', True),
])
def test_write_guard_hosts(monkeypatch, dsn, allowed, ok):
    monkeypatch.setenv('POPPYS_ORACLE_DSN', dsn); monkeypatch.setenv('POPPYS_ORACLE_ALLOWED_HOSTS', allowed)
    monkeypatch.setenv('POPPYS_ORACLE_DB_IDENTITY', '')
    assert (oracle_store.write_problem() is None) is ok


def test_a_save_to_another_computer_is_refused_before_connecting(monkeypatch):
    monkeypatch.setenv('POPPYS_ORACLE_DSN', '192.0.2.10:1521/CLIENTPROD'); monkeypatch.setenv('POPPYS_ORACLE_ALLOWED_HOSTS', '')
    monkeypatch.setattr(oracle_store, 'connect', lambda *a, **k: pytest.fail('connected to a database it may not write'))
    with pytest.raises(RuntimeError, match='saves orders only to the database on this computer, and ORACLE_DSN points at 192.0.2.10'):
        oracle_store.commit(dict(id='x', filehash='0' * 64, orders=[order()]), '0' * 64, 'tester', mapping=registry.effective())


def test_a_pinned_database_identity_must_match(writer, monkeypatch):
    monkeypatch.setenv('POPPYS_ORACLE_DB_IDENTITY', 'PRODPDB')
    with pytest.raises(RuntimeError, match=r'only to the database PRODPDB .*connection is to FREEPDB1 \(DB_UNIQUE_NAME FREE\)'):
        writer.save(order())
    assert not writer.inserted and not any('PP_WRITE_LOCK' in s or 'PP_ORDER_KEYS' in s for s in writer.sql)
    for name in ('freepdb1', 'FREE'):                       # CON_NAME or DB_UNIQUE_NAME, any case
        monkeypatch.setenv('POPPYS_ORACLE_DB_IDENTITY', name)
        assert writer.save(order())['orders']


# --- finding 10: READ, not SELECT, for what POPPYS_APP only reads ---------------------------------------------------
@pytest.fixture
def scripts(monkeypatch):
    """(scripts/bootstrap_local.py, scripts/grant_bom_tables.py, the SQL they send); nothing reaches a database."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import bootstrap_local
    import grant_bom_tables
    return bootstrap_local, grant_bom_tables, []


def test_bootstrap_grants_read_and_insert_and_pins_the_database(scripts, tmp_path, monkeypatch):
    bootstrap, _, calls = scripts
    monkeypatch.setattr(bootstrap, 'CONFIG', tmp_path / '.env.poppys')
    monkeypatch.setattr(bootstrap, 'sqlplus', lambda sql: calls.append(sql) or ('0' if 'dba_users' in sql else '19' if 'dba_tables' in sql else ''))
    bootstrap.main()
    ddl = calls[-1]
    for t in bootstrap.TABLES:
        assert f"grant read on GARMENT_DEMO.{t} to POPPYS_APP" in ddl and f'grant insert on GARMENT_DEMO.{t} to POPPYS_APP;' in ddl
    assert 'grant select, insert' not in ddl
    assert ddl.count('grant select on') == ddl.count('if sqlcode = -990 then execute immediate \'grant select on') == len(bootstrap.TABLES)
    assert 'ORACLE_DB_IDENTITY=FREEPDB1' in (tmp_path / '.env.poppys').read_text(encoding='utf-8')


@pytest.mark.parametrize('kept', ['', 'PARTYMAS ORDMAS'])
def test_grant_script_replaces_select_only_where_read_was_granted(scripts, monkeypatch, capsys, kept):
    _, grants, calls = scripts

    def sqlplus(sql):
        calls.append(sql)
        if 'dba_users' in sql:
            return '1'
        if sql.startswith('select table_name'):
            return 'ORDMAS\nORDFAB\nORDYARN\nORDACC\nPARTYMAS'
        return f'READ=5\nSELECT=0\nNARROWED={5 - len(kept.split())}\nKEPT={kept}'
    monkeypatch.setattr(grants, 'sqlplus', sqlplus)
    grants.main()
    block = calls[-1]
    read, revoke, fallback = (block.index(s) for s in ("'grant read on", "'revoke select on", 'if sqlcode = -990'))
    assert read < block.index('if has_select') < revoke < fallback      # revoked only after READ was granted on that table
    assert 'grant insert on GARMENT_DEMO.ORDFAB to POPPYS_APP;' in block
    out = capsys.readouterr().out
    assert 'Granted READ on 5 GARMENT_DEMO tables' in out and 'Replaced the older SELECT grant with READ' in out
    assert ('REVOKE SELECT ON GARMENT_DEMO.<table> FROM POPPYS_APP' in out and 'PARTYMAS, ORDMAS' in out) is bool(kept)


def test_preflight_warns_when_the_main_account_reads_with_select(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import check_oracle
    from test_oracle_pool import DictConn
    from backend.erp import catalog
    monkeypatch.setattr(catalog.OracleCatalog, 'columns', lambda self, tables: {})
    monkeypatch.setattr(catalog.OracleCatalog, 'insertable', lambda self, tables: {'ORDMAS'})
    for privilege, warned in (('SELECT', True), ('READ', False)):
        report = check_oracle.Report()
        check_oracle.check_main(DictConn('POPPYS_APP', [('GARMENT_DEMO', 'ORDMAS', 'INSERT'), ('GARMENT_DEMO', 'PARTYMAS', privilege)]),
                                report, 'GARMENT_DEMO')
        out = capsys.readouterr().out
        assert ('SELECT rather than READ on 1 GARMENT_DEMO tables (PARTYMAS)' in out) is warned and report.failed == 0
