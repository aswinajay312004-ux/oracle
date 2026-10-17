"""Additive, idempotent grants for the restricted POPPYS_APP account. Run once on the laptop (again at any time):

    python scripts/grant_bom_tables.py

* READ on every GARMENT_DEMO table (read-only Database explorer and live read-back). READ is SELECT without
  SELECT ... FOR UPDATE and LOCK TABLE, so POPPYS_APP cannot lock Axpert rows it only reads (Oracle 12.1.0.2+;
  older versions have no READ and get SELECT).
* INSERT on the order/BOM child tables POPPYS AI can write after you verify their mapping
  (ORDFAB, ORDYARN, ORDACC) in addition to the order/master tables granted by bootstrap.
* An older SELECT grant (bootstrap and this script granted SELECT before) is revoked, but only from a table where
  READ is now granted, so every read keeps working; POPPYS AI never locks GARMENT_DEMO rows. A SELECT that cannot be
  revoked is listed with the statement for the DBA.

Nothing is dropped, reset or updated. No GARMENT_DEMO data is changed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import sqlplus  # noqa: E402

INSERT_TABLES = ('ORDFAB', 'ORDYARN', 'ORDACC')

READ_GRANTS = '''set serveroutput on
declare
  n_read pls_integer := 0; n_select pls_integer := 0; n_narrowed pls_integer := 0; kept varchar2(4000);
  function has_select(t varchar2) return boolean is
    n pls_integer;
  begin
    select count(*) into n from dba_tab_privs
     where grantee = 'POPPYS_APP' and owner = 'GARMENT_DEMO' and table_name = t and privilege = 'SELECT';
    return n > 0;
  end;
begin
  for t in (select table_name from dba_tables where owner = 'GARMENT_DEMO' order by table_name) loop
    begin
      execute immediate 'grant read on GARMENT_DEMO."' || t.table_name || '" to POPPYS_APP';
      n_read := n_read + 1;
      if has_select(t.table_name) then
        begin
          execute immediate 'revoke select on GARMENT_DEMO."' || t.table_name || '" from POPPYS_APP';
        exception when others then null;   -- e.g. granted by another user: listed below
        end;
        if has_select(t.table_name) then kept := kept || ' ' || t.table_name; else n_narrowed := n_narrowed + 1; end if;
      end if;
    exception when others then
      if sqlcode = -990 then   -- no READ privilege before Oracle 12.1.0.2
        execute immediate 'grant select on GARMENT_DEMO."' || t.table_name || '" to POPPYS_APP';
        n_select := n_select + 1;
      else raise; end if;
    end;
  end loop;
  dbms_output.put_line('READ=' || n_read);
  dbms_output.put_line('SELECT=' || n_select);
  dbms_output.put_line('NARROWED=' || n_narrowed);
  dbms_output.put_line('KEPT=' || trim(kept));
end;
/'''


def main():
    if sqlplus("select count(*) from dba_users where username='POPPYS_APP';").strip() != '1':
        raise SystemExit('POPPYS_APP does not exist yet. Run python scripts/bootstrap_local.py first.')
    present = set(sqlplus("select table_name from dba_tables where owner='GARMENT_DEMO';").split())
    missing = [t for t in INSERT_TABLES if t not in present]
    if missing:
        print('Not present in GARMENT_DEMO (skipped): ' + ', '.join(missing))
    out = sqlplus(READ_GRANTS + '\n' + '\n'.join(f'grant insert on GARMENT_DEMO.{t} to POPPYS_APP;' for t in INSERT_TABLES if t in present))
    got = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    print(f'Granted READ on {got.get("READ", "?")} GARMENT_DEMO tables and INSERT on '
          f'{", ".join(t for t in INSERT_TABLES if t in present)} to POPPYS_APP. No data was changed.')
    if got.get('NARROWED', '0') != '0':
        print(f'Replaced the older SELECT grant with READ on {got["NARROWED"]} tables (SELECT also allowed SELECT ... FOR UPDATE '
              'and LOCK TABLE on Axpert data).')
    if got.get('SELECT', '0') != '0':
        print(f'This Oracle has no READ privilege (before 12.1.0.2), so {got["SELECT"]} tables were granted SELECT instead.')
    if got.get('KEPT'):
        print('SELECT could not be revoked on: ' + ', '.join(got['KEPT'].split()) + ' (it was granted by another user). READ is '
              'in place, so the DBA can run, for each: REVOKE SELECT ON GARMENT_DEMO.<table> FROM POPPYS_APP;')


if __name__ == '__main__':
    main()
