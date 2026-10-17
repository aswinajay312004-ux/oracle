"""Oracle preflight and connection self-test, using the application's own accounts (.env.poppys) and pools.

    python scripts/check_oracle.py

Checks, in plain English, and exits with status 1 when one fails:
* the Oracle version: 12.1 or later (POPPYS AI's queries use FETCH FIRST), and which database this is;
* whether approved orders may be saved to it (ORACLE_DSN on this computer or in ORACLE_ALLOWED_HOSTS, ORACLE_DB_IDENTITY);
* the main account (POPPYS_APP): what it can see and insert, that it can NOT update or delete GARMENT_DEMO data, and
  that it reads with READ (SELECT would also let it lock Axpert rows);
* the optional read-only account (POPPYS_RO, scripts/create_readonly_user.py): that it can only read;
* the connection pool settings, by taking a connection from each of the app's own pools.
Read-only: nothing is written.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WRITE_ANY = re.compile(r'^(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|LOCK|CREATE|MERGE) ANY ')
# Granted on the user itself (by default to PUBLIC) and about invoker's-rights code, not about reading or writing data.
USER_LEVEL = {'INHERIT PRIVILEGES', 'INHERIT REMOTE PRIVILEGES'}
HELP = {
    'DPY-3015': 'This Oracle account still has an old 10G password verifier, which python-oracledb Thin mode cannot use. '
                'Ask the DBA to reset its password (with the 11G/12C verifier enabled), then update .env.poppys.',
    'ORA-01017': 'Oracle refused the user name or password in .env.poppys.',
    'ORA-28000': 'The account is locked. Ask the DBA to unlock it.',
    'ORA-28001': 'The account password has expired. Ask the DBA to reset it, then update .env.poppys.',
    'DPY-6005': 'Nothing answered at the configured address. Check Docker Desktop, the Oracle container and ORACLE_DSN.',
    'DPY-4011': 'The database closed the connection. Check that the Oracle container is healthy.',
}


class Report:
    def __init__(self):
        self.failed = 0; self.warned = 0

    def ok(self, text):
        print(f'  PASS  {text}')

    def warn(self, text):
        self.warned += 1; print(f'  WARN  {text}')

    def fail(self, text):
        self.failed += 1; print(f'  FAIL  {text}')

    def info(self, text):
        print(f'        {text}')


def explain(exc):
    from backend import oracle_store as oracle
    code = oracle._code(exc)
    return f'{HELP[code]} ({code})' if code in HELP else str(exc).splitlines()[0][:300]


def privileges(conn, schemas):
    """{(schema, object): {privilege}} granted to this session (directly, to PUBLIC or to an enabled role) on the
    given schemas, and the session's system privileges."""
    c = conn.cursor(); found = {}
    marks = ','.join(f':s{i}' for i in range(len(schemas)))
    c.execute(f'''select table_schema, table_name, privilege from all_tab_privs
                   where table_schema in ({marks})
                     and (grantee in (user, 'PUBLIC') or grantee in (select role from session_roles))''',
              {f's{i}': s for i, s in enumerate(schemas)})
    for schema, table, privilege in c:
        if privilege not in USER_LEVEL:
            found.setdefault((schema, table), set()).add(privilege)
    c.execute('select privilege from session_privs')
    return found, sorted(r[0] for r in c)


def visible(conn, owner):
    c = conn.cursor()
    c.execute('select distinct table_name from all_tab_columns where owner = :o', o=owner)
    return {r[0] for r in c}


def check_server(conn, report):
    from backend import oracle_store as oracle
    from backend.config import setting
    c = conn.cursor()
    c.execute("select sys_context('USERENV','DB_UNIQUE_NAME'), sys_context('USERENV','CON_NAME') from dual")
    unique, container = c.fetchone()
    print(f'Database: Oracle {conn.version}, {container} (DB_UNIQUE_NAME {unique}) at {conn.dsn}')
    problem = oracle.version_problem(conn.version)
    if problem:
        report.fail(problem)
    else:
        report.ok(f'Oracle {conn.version} is supported (POPPYS AI needs 12.1 or later for FETCH FIRST).')
    problem = oracle.write_problem(conn)      # the guard commit() applies before it writes anything
    pinned = setting('ORACLE_DB_IDENTITY').strip().upper()
    if problem:
        report.warn('Approved orders will not be saved here: ' + problem.removeprefix('Nothing was saved: '))
    else:
        report.ok(f'Approved orders may be saved here: ORACLE_DSN names {", ".join(oracle.dsn_hosts(setting("ORACLE_DSN", oracle.DEFAULT_DSN)))}'
                  + (f' and the database is {pinned} (ORACLE_DB_IDENTITY).' if pinned else
                     f'. Set ORACLE_DB_IDENTITY={container} in .env.poppys to also pin the database.'))


def check_main(conn, report, owner):
    from backend.erp import mapping as registry
    from backend.erp.catalog import OracleCatalog
    print(f'Main account {conn.username} (saves orders)')
    m = registry.effective()
    cat = OracleCatalog(conn); tables = registry.referenced_tables(m)
    seen = cat.columns(tables); insertable = cat.insertable(tables)
    for t in sorted(tables):
        report.info(f'{t:10} visible={"yes" if t in seen else "NO ":3}  insert={"yes" if t in insertable else "no"}')
    c = conn.cursor()
    for obj in ('PP_IMPORT_LOG', 'PP_ORDER_KEYS', 'PP_WRITE_LOCK'):
        c.execute(f'select count(*) from {obj}')
        report.info(f'{obj}: {c.fetchone()[0]} rows')
    grants, system = privileges(conn, [owner])
    changes = sorted(f'{p} on {t}' for (_, t), ps in grants.items() for p in ps if p in ('UPDATE', 'DELETE'))
    risky = [p for p in system if WRITE_ANY.match(p)]
    if changes or risky:
        report.fail(f'{conn.username} can change or delete existing {owner} data, which POPPYS AI never needs: '
                    f'{", ".join(changes + risky)}. Ask the DBA to revoke these.')
    else:
        report.ok(f'{conn.username} cannot UPDATE or DELETE {owner} data (it may only read and insert).')
    selects = sorted(t for (_, t), ps in grants.items() if 'SELECT' in ps)
    if selects:
        report.warn(f'{conn.username} has SELECT rather than READ on {len(selects)} {owner} tables ({", ".join(selects[:5])}'
                    f'{" …" if len(selects) > 5 else ""}): SELECT also allows SELECT ... FOR UPDATE and LOCK TABLE on Axpert data. '
                    'Run python scripts/grant_bom_tables.py, which grants READ instead.')
    if not insertable:
        report.warn(f'{conn.username} has no INSERT permission yet, so approved orders cannot be saved. '
                    'Run python scripts/grant_bom_tables.py.')
    return visible(conn, owner)


def check_readonly(conn, report, owner, app_user, app_tables):
    print(f'Read-only account {conn.username} (Database screen, Ask AI, history, read-back)')
    grants, system = privileges(conn, [owner, app_user])
    writes = sorted(f'{p} on {s}.{t}' for (s, t), ps in grants.items() for p in ps if p not in ('READ', 'SELECT'))
    extra = [p for p in system if p != 'CREATE SESSION']
    if writes or extra:
        report.fail(f'{conn.username} should only read, but it also has: {", ".join(writes + extra)}. '
                    'Ask the DBA to revoke these.')
    else:
        n = sum(1 for (s, _) in grants if s == owner)
        report.ok(f'{conn.username} can only read: READ/SELECT on {n} {owner} objects'
                  f'{" and PP_IMPORT_LOG" if (app_user, "PP_IMPORT_LOG") in grants else ""}, '
                  'CREATE SESSION, and no INSERT, UPDATE or DELETE.')
    if any('SELECT' in ps for ps in grants.values()):
        report.warn('Some grants are SELECT rather than READ: SELECT also allows SELECT ... FOR UPDATE (row locks). '
                    'READ is enough for every page.')
    mine = visible(conn, owner)
    if mine != app_tables:
        report.warn(f'It sees {len(mine)} {owner} tables, the main account {len(app_tables)}. '
                    'Run python scripts/create_readonly_user.py again to bring its grants in step.')
    else:
        report.ok(f'It sees the same {len(mine)} {owner} tables as {app_user}.')
    c = conn.cursor(); c.execute('select * from user_users')
    row = dict(zip([d[0] for d in c.description], c.fetchone()))
    if row.get('READ_ONLY') == 'YES':
        report.ok('It is a READ ONLY user (Oracle 23ai): Oracle refuses any write by it (ORA-28194).')
    c.execute('SELECT COUNT(*) FROM PP_IMPORT_LOG')
    report.info(f'Saved orders history (PP_IMPORT_LOG) readable: {c.fetchone()[0]} receipts.')


def main():
    from backend import oracle_store as oracle
    from backend.config import OWNER, setting
    report = Report()
    print('POPPYS AI: Oracle preflight (read-only checks)')
    try:
        main_conn = oracle.connect(purpose='preflight')
    except Exception as exc:
        raise SystemExit(f'FAILED to connect as {setting("ORACLE_USER") or "the main account"}: {explain(exc)}\n'
                         'Check Docker Desktop, the Oracle container and .env.poppys.')
    with main_conn as conn:
        check_server(conn, report)
        app_user = conn.username.upper()
        app_tables = check_main(conn, report, OWNER)
    if oracle.readonly_configured():
        try:
            with oracle.connect(purpose='preflight', readonly=True) as conn:
                check_readonly(conn, report, OWNER, app_user, app_tables)
        except Exception as exc:
            print(f'Read-only account {setting("ORACLE_RO_USER")}')
            report.fail(f'Could not connect: {explain(exc)}')
    else:
        print('Read-only account: not configured (optional)')
        report.info(f'Read-only pages use {app_user}. To add one: python scripts/create_readonly_user.py, then restart POPPYS AI.')
    p = oracle.POOL
    print('Connection pools (one per account)')
    report.info(f'min {p["min"]}, max {p["max"]}, increment {p["increment"]}; a request waits at most {p["wait_timeout"] / 1000:g} s '
                f'for a free session (TIMEDWAIT), then gets "busy, try again" (HTTP 503)')
    report.info(f'sessions idle {p["ping_interval"]} s are pinged before reuse; idle sessions above min close after {p["timeout"]} s; '
                f'every session is renewed after {p["max_lifetime_session"] // 3600} h')
    report.info(f'call timeout {oracle.CALL_TIMEOUT_MS / 1000:g} s per round trip unless a page sets its own; '
                f'sessions are traced as MODULE {oracle.MODULE!r} with the page as ACTION and the user as CLIENT_IDENTIFIER')
    for s in oracle.pool_stats():
        report.info(f'{s["account"]}{" (read-only)" if s["readonly"] else ""}: {s["opened"]} open, {s["busy"]} busy, at most {s["max"]}')
    oracle.close_pool()
    if report.failed:
        raise SystemExit(f'{report.failed} check(s) FAILED{f", {report.warned} warning(s)" if report.warned else ""}. Nothing was written.')
    print(f'All checks passed{f" ({report.warned} warning(s))" if report.warned else ""}. Nothing was written.')


if __name__ == '__main__':
    main()
