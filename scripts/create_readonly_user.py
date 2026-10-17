"""Optional, additive: a read-only Oracle account (POPPYS_RO) for the pages of POPPYS AI that only read: the
Database explorer, Ask AI, import history and read-back. With it, a mistake or a crafted question on those pages
cannot change data even in principle, because the account has no INSERT, UPDATE or DELETE on anything.

    python scripts/create_readonly_user.py        (Docker container: POPPYS_ORACLE_CONTAINER, default oracle26ai)

As SYSDBA inside the container, like scripts/bootstrap_local.py, it:
* creates POPPYS_RO with a generated password and CREATE SESSION only. The password is appended to .env.poppys
  (ORACLE_RO_USER / ORACLE_RO_PASSWORD) before the account is created, and is never printed;
* grants READ on each GARMENT_DEMO table or view the main account (POPPYS_APP) can read, so both accounts see the
  same data, and on the main account's PP_IMPORT_LOG (import history), with a private synonym so the app's queries
  find it. READ is SELECT without SELECT ... FOR UPDATE and LOCK TABLE (Oracle 12.1.0.2+; SELECT before that);
* on Oracle 23ai and later also makes it a READ ONLY user, so any write fails with ORA-28194.

It never resets an existing user or password. Running it again only adds READ grants for tables granted to
POPPYS_APP since (for example after scripts/grant_bom_tables.py). No GARMENT_DEMO data is changed.
Restart POPPYS AI afterwards; scripts/check_oracle.py then checks both accounts.
"""
import os
import re
import secrets
import sys
from pathlib import Path
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import CONFIG, sqlplus  # noqa: E402

OWNER = 'GARMENT_DEMO'
NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,29}$')


def _user(value, what):
    value = (value or '').strip().upper()
    if not NAME.match(value):
        raise SystemExit(f'{what} must be a plain Oracle user name (letters, digits, _), not {value!r}.')
    return value


def _count(sql):
    out = sqlplus(sql).split()
    return int(out[-1]) if out and out[-1].isdigit() else -1


def _append(lines):
    """Adds settings to .env.poppys without touching what is there."""
    existing = CONFIG.read_text(encoding='utf-8')
    with CONFIG.open('a', encoding='utf-8') as f:
        f.write(('\n' if existing and not existing.endswith('\n') else '') + ''.join(f'{k}={v}\n' for k, v in lines))


def grant_sql(app, ro):
    """PL/SQL that brings the read-only account's grants in step with what the main account can read. Additive:
    nothing is revoked. Prints TABLES=<n> and READONLY=yes|no|n/a."""
    return f'''set serveroutput on
declare
  n pls_integer := 0;
  ro_only varchar2(3) := 'n/a';
  procedure grant_read(obj varchar2) is
  begin
    execute immediate 'grant read on ' || obj || ' to {ro}';
  exception when others then
    if sqlcode = -990 then execute immediate 'grant select on ' || obj || ' to {ro}';   -- no READ privilege before 12.1.0.2
    else raise; end if;
  end;
begin
  execute immediate 'grant create session to {ro}';   -- again, in case an earlier run stopped half-way
  for t in (select distinct p.table_name from dba_tab_privs p
              join dba_objects o on o.owner = p.owner and o.object_name = p.table_name
             where p.grantee = '{app}' and p.owner = '{OWNER}' and p.privilege in ('SELECT', 'READ')
               and o.object_type in ('TABLE', 'VIEW', 'MATERIALIZED VIEW')) loop
    grant_read('{OWNER}."' || t.table_name || '"');
    n := n + 1;
  end loop;
  grant_read('{app}.PP_IMPORT_LOG');
  execute immediate 'create or replace synonym {ro}.PP_IMPORT_LOG for {app}.PP_IMPORT_LOG';
  if dbms_db_version.version >= 23 then
    begin
      execute immediate 'alter user {ro} read only';
      ro_only := 'yes';
    exception when others then ro_only := 'no';
    end;
  end if;
  dbms_output.put_line('TABLES=' || n);
  dbms_output.put_line('READONLY=' || ro_only);
end;
/'''


def main():
    if not CONFIG.exists():
        raise SystemExit('.env.poppys does not exist yet. Run python scripts/bootstrap_local.py first.')
    config = dotenv_values(CONFIG)
    app = _user(config.get('ORACLE_USER') or 'POPPYS_APP', 'ORACLE_USER')
    ro = _user(config.get('ORACLE_RO_USER') or os.environ.get('POPPYS_RO_USER') or 'POPPYS_RO', 'ORACLE_RO_USER')
    if ro == app:
        raise SystemExit(f'The read-only account must be a different user from {app}.')
    if _count(f"select count(*) from dba_users where username='{app}';") != 1:
        raise SystemExit(f'{app} does not exist yet. Run python scripts/bootstrap_local.py first.')
    if _count(f"select count(*) from dba_tables where owner='{app}' and table_name='PP_IMPORT_LOG';") != 1:
        raise SystemExit(f'{app}.PP_IMPORT_LOG is missing. Run python scripts/bootstrap_local.py first.')
    exists = _count(f"select count(*) from dba_users where username='{ro}';") == 1
    password = config.get('ORACLE_RO_PASSWORD')
    if exists and not password:
        raise SystemExit(f'{ro} already exists, but .env.poppys has no ORACLE_RO_PASSWORD. Its password will not be reset: '
                         'restore the .env.poppys lines that created it, or ask the DBA.')
    if not exists:
        missing = []
        if not config.get('ORACLE_RO_USER'):
            missing.append(('ORACLE_RO_USER', ro))
        if not password:
            password = 'Pp' + secrets.token_hex(24)
            missing.append(('ORACLE_RO_PASSWORD', password))
        _append(missing)   # saved before the DDL, so a partial setup never loses the generated credential
        # No default tablespace or quota: the account cannot create anything.
        sqlplus(f'create user {ro} identified by "{password}";\ngrant create session to {ro};')
    out = dict(line.split('=', 1) for line in sqlplus(grant_sql(app, ro)).splitlines() if '=' in line)
    tables = out.get('TABLES', '?')
    readonly = {'yes': ' It is a READ ONLY user (Oracle 23ai): any write is refused with ORA-28194.',
                'no': ' (Could not make it a READ ONLY user; its grants alone keep it read-only.)'}.get(out.get('READONLY'), '')
    if exists:
        print(f'{ro} already exists; its password was not changed. Its READ grants match {app}: {tables} {OWNER} tables '
              f'and PP_IMPORT_LOG.{readonly}')
    else:
        print(f'Created the read-only account {ro}: CREATE SESSION and READ on {tables} {OWNER} tables and PP_IMPORT_LOG, '
              f'nothing else.{readonly} Its settings were added to .env.poppys (password not shown). Restart POPPYS AI to use it '
              'for the Database screen, Ask AI, history and read-back, then run python scripts/check_oracle.py.')


if __name__ == '__main__':
    main()
