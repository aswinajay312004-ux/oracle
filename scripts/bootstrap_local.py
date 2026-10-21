"""One-time additive setup for the EXISTING local demo. Never resets existing users."""
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.env.poppys'
TABLES = ('BUYMAS','STYLENUM','STYLEMAS','SIZE_MAS','COLORMAS','UNITMAS','MERCHMAS','ORDMAS','ORDSTYLE','ORDSIZE')

def sqlplus(sql):
    result = subprocess.run(['docker','exec','-i','oracle26ai','sqlplus','-s','/ as sysdba'],
        input='set echo off feedback off heading off pages 0 lines 32767\nwhenever sqlerror exit failure rollback\nalter session set container=FREEPDB1;\n'+sql+'\nexit\n',
        text=True, capture_output=True, timeout=90)
    if result.returncode:
        # Never echo DDL, since CREATE USER contains a generated credential.
        raise RuntimeError('Local Oracle setup failed. Check Docker / FREEPDB1 availability; credentials were not logged.')
    return result.stdout.strip()

def main():
    if CONFIG.exists():
        print('Local configuration already exists. No database changes made.')
        return
    if sqlplus("select count(*) from dba_users where username='POPPYS_APP';").strip() != '0':
        raise RuntimeError('POPPYS_APP already exists. Restore its .env.poppys; password will not be reset.')
    if sqlplus("select count(*) from dba_tables where owner='GARMENT_DEMO';").strip() != '19':
        raise RuntimeError('Expected existing 19-table GARMENT_DEMO. Inspect schema before setup.')
    password = 'Pp' + secrets.token_hex(24)
    # Save before DDL so a partial setup never loses the generated credential.
    CONFIG.write_text('ORACLE_USER=POPPYS_APP\nORACLE_PASSWORD='+password+
        '\nORACLE_DSN=127.0.0.1:1521/FREEPDB1\nORACLE_OWNER=GARMENT_DEMO\nOLLAMA_MODEL=qwen2.5:7b-instruct\n', encoding='utf-8')
    grants = '\n'.join(f'grant select, insert on GARMENT_DEMO.{t} to POPPYS_APP;' for t in TABLES)
    sqlplus(f'''create user POPPYS_APP identified by "{password}" default tablespace USERS quota 50M on USERS;
grant create session to POPPYS_APP;
{grants}
create sequence POPPYS_APP.PP_RECORD_SEQ start with 1000000000000 increment by 1 nocycle cache 100;
create table POPPYS_APP.PP_IMPORT_LOG (
 JOBID varchar2(64) primary key, FILEHASH varchar2(64) not null,
 PLANHASH varchar2(64) not null, APPROVER varchar2(50) not null,
 COMMITTED_AT timestamp default systimestamp not null, RESULT_JSON clob not null);
create table POPPYS_APP.PP_ORDER_KEYS (ORDER_REF varchar2(30) primary key, JOBID varchar2(64) not null);
create table POPPYS_APP.PP_WRITE_LOCK (ID number primary key);
insert into POPPYS_APP.PP_WRITE_LOCK values (1);
commit;''')
    print('Created limited POPPYS_APP connection, sequence and import audit tables. Existing GARMENT_DEMO records preserved.')

if __name__ == '__main__':
    main()
