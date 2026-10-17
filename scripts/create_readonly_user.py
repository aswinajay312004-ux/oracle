"""Creates (or refreshes) the separate READ-ONLY Oracle account used by Ask AI and the explorer. Run once on
the laptop, after bootstrap_local.py:

    python scripts/create_readonly_user.py

* Account POPPYS_RO: CREATE SESSION plus SELECT on every GARMENT_DEMO table and view. Nothing else, so
  Oracle itself refuses any INSERT, UPDATE, DELETE or DDL from Ask AI, whatever SQL reached it.
* Its generated password is appended to the git-ignored .env.poppys (ORACLE_RO_USER / ORACLE_RO_PASSWORD).
* Safe to run again: an existing account keeps its password and only gets SELECT on new tables.

No GARMENT_DEMO data is read or changed.
"""
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import CONFIG, sqlplus  # noqa: E402

USER = 'POPPYS_RO'


def configured():
    if not CONFIG.exists():
        return {}
    pairs = (line.split('=', 1) for line in CONFIG.read_text(encoding='utf-8').splitlines() if '=' in line)
    return {k.strip(): v.strip() for k, v in pairs}


def main():
    if not CONFIG.exists():
        raise SystemExit('Run python scripts/bootstrap_local.py first (it creates .env.poppys).')
    exists = sqlplus(f"select count(*) from dba_users where username='{USER}';").strip() == '1'
    have = configured().get('ORACLE_RO_PASSWORD')
    if exists and not have:
        raise SystemExit(f'{USER} exists but its password is not in .env.poppys. Restore that line, or ask the DBA to '
                         f'drop {USER} and run this script again. Nothing was changed.')
    if not exists:
        password = 'Ro' + secrets.token_hex(24)
        # Save before DDL so a partial setup never loses the generated credential.
        with CONFIG.open('a', encoding='utf-8') as f:
            f.write(f'\nORACLE_RO_USER={USER}\nORACLE_RO_PASSWORD={password}\n')
        sqlplus(f'create user {USER} identified by "{password}" default tablespace USERS quota 0 on USERS;\n'
                f'grant create session to {USER};')
    sqlplus(f'''begin
  for t in (select object_name from dba_objects where owner='GARMENT_DEMO' and object_type in ('TABLE','VIEW')) loop
    execute immediate 'grant select on GARMENT_DEMO."' || t.object_name || '" to {USER}';
  end loop;
end;
/''')
    tables = sqlplus(f"select count(*) from dba_tab_privs where grantee='{USER}' and owner='GARMENT_DEMO' and privilege='SELECT';").strip()
    other = sqlplus(f"select count(*) from dba_tab_privs where grantee='{USER}' and privilege<>'SELECT';").strip()
    print(f'{USER}: SELECT on {tables} GARMENT_DEMO tables/views; other object privileges: {other}. '
          f'{"Created" if not exists else "Refreshed"}; no data was changed.')
    if other != '0':
        raise SystemExit(f'{USER} has privileges beyond SELECT. Ask AI will refuse to use it until they are revoked.')


if __name__ == '__main__':
    main()
