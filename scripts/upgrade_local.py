"""Additive, re-runnable upgrade of the local POPPYS_APP account for order editing,
database exploration and admin-approved schema changes.

Never drops, truncates or rewrites GARMENT_DEMO data. Each step checks whether it is
already applied. Run after scripts/bootstrap_local.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import CONFIG, sqlplus  # noqa: E402

UPDATABLE = ('ORDMAS', 'ORDSTYLE', 'ORDSIZE')

# Definer-rights helper owned by GARMENT_DEMO. The application builds DDL only from
# validated structured input; this procedure is a second, independent guard that accepts
# exactly one CREATE TABLE or one ADD FOREIGN KEY statement inside GARMENT_DEMO.
APPLY_DDL = r'''create or replace procedure GARMENT_DEMO.PP_APPLY_DDL(p_kind varchar2, p_table varchar2, p_ddl clob) is
  l_table varchar2(128) := dbms_assert.simple_sql_name(p_table);
  l_ddl varchar2(32767) := dbms_lob.substr(p_ddl, 32767, 1);
begin
  if dbms_lob.getlength(p_ddl) > 32000 or instr(l_ddl, ';') > 0 or instr(l_ddl, '--') > 0 or instr(l_ddl, '/*') > 0 then
    raise_application_error(-20901, 'Rejected schema statement.');
  end if;
  if p_kind = 'CREATE_TABLE' and l_ddl like 'CREATE TABLE "GARMENT_DEMO"."' || l_table || '" (%' then
    execute immediate l_ddl;
    execute immediate 'grant select, insert, update on "GARMENT_DEMO"."' || l_table || '" to POPPYS_APP';
  elsif p_kind = 'ADD_FOREIGN_KEY' and l_ddl like 'ALTER TABLE "GARMENT_DEMO"."' || l_table || '" ADD CONSTRAINT "PP_FK_%" FOREIGN KEY (%) REFERENCES "GARMENT_DEMO".%' then
    execute immediate l_ddl;
  else
    raise_application_error(-20902, 'Only approved CREATE TABLE or FOREIGN KEY statements are accepted.');
  end if;
end;
/'''


def exists(sql):
    return sqlplus(sql).strip() != '0'


def main():
    if not CONFIG.exists():
        raise SystemExit('Run scripts/bootstrap_local.py first.')
    steps = []
    # Read-only browsing of every GARMENT_DEMO table for the Database explorer.
    sqlplus('''begin
  for t in (select table_name from dba_tables where owner = 'GARMENT_DEMO') loop
    execute immediate 'grant select on "GARMENT_DEMO"."' || t.table_name || '" to POPPYS_APP';
  end loop;
end;
/''')
    steps.append('read access to GARMENT_DEMO tables')
    sqlplus('\n'.join(f'grant update on GARMENT_DEMO.{t} to POPPYS_APP;' for t in UPDATABLE))
    steps.append('approved order updates on ' + ', '.join(UPDATABLE))
    if not exists("select count(*) from dba_tables where owner='POPPYS_APP' and table_name='PP_CHANGE_LOG';"):
        sqlplus('''create table POPPYS_APP.PP_CHANGE_LOG (
 ID number generated always as identity primary key, CHANGESET varchar2(64) not null,
 ORDMASID number not null, TABLE_NAME varchar2(128) not null, ROW_ID number not null,
 COLUMN_NAME varchar2(128) not null, OLD_VALUE varchar2(4000), NEW_VALUE varchar2(4000),
 APPROVER varchar2(50) not null, CHANGED_AT timestamp default systimestamp not null);
create index POPPYS_APP.PP_CHANGE_LOG_ORD on POPPYS_APP.PP_CHANGE_LOG(ORDMASID);
create index POPPYS_APP.PP_CHANGE_LOG_SET on POPPYS_APP.PP_CHANGE_LOG(CHANGESET);''')
        steps.append('order change audit table')
    if not exists("select count(*) from dba_tables where owner='POPPYS_APP' and table_name='PP_SCHEMA_LOG';"):
        sqlplus('''create table POPPYS_APP.PP_SCHEMA_LOG (
 ID number generated always as identity primary key, ACTION varchar2(30) not null,
 OBJECT_NAME varchar2(128) not null, DDL clob not null, APPROVER varchar2(50) not null,
 EXECUTED_AT timestamp default systimestamp not null);''')
        steps.append('schema change audit table')
    # Definer-rights procedures need direct (not role) privileges.
    sqlplus('grant create table to GARMENT_DEMO;\n' + APPLY_DDL + '\ngrant execute on GARMENT_DEMO.PP_APPLY_DDL to POPPYS_APP;')
    steps.append('admin-approved schema change helper')
    print('Upgrade complete: ' + '; '.join(steps) + '. Existing GARMENT_DEMO records preserved.')


if __name__ == '__main__':
    main()
