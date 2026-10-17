"""Additive, idempotent grants for the restricted POPPYS_APP account. Run once on the laptop:

    python scripts/grant_bom_tables.py

* SELECT on every GARMENT_DEMO table (read-only Database explorer and live read-back).
* INSERT on the order/BOM child tables POPPYS AI can write after you verify their mapping
  (ORDFAB, ORDYARN, ORDACC) in addition to the order/master tables granted by bootstrap.

Nothing is dropped, reset or updated. No GARMENT_DEMO data is changed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import sqlplus  # noqa: E402

INSERT_TABLES = ('ORDFAB', 'ORDYARN', 'ORDACC')


def main():
    if sqlplus("select count(*) from dba_users where username='POPPYS_APP';").strip() != '1':
        raise SystemExit('POPPYS_APP does not exist yet. Run python scripts/bootstrap_local.py first.')
    present = set(sqlplus("select table_name from dba_tables where owner='GARMENT_DEMO';").split())
    missing = [t for t in INSERT_TABLES if t not in present]
    if missing:
        print('Not present in GARMENT_DEMO (skipped): ' + ', '.join(missing))
    sqlplus('''begin
  for t in (select table_name from dba_tables where owner='GARMENT_DEMO') loop
    execute immediate 'grant select on GARMENT_DEMO."' || t.table_name || '" to POPPYS_APP';
  end loop;
end;
/''' + '\n' + '\n'.join(f'grant insert on GARMENT_DEMO.{t} to POPPYS_APP;' for t in INSERT_TABLES if t in present))
    print(f'Granted SELECT on {len(present)} GARMENT_DEMO tables and INSERT on '
          f'{", ".join(t for t in INSERT_TABLES if t in present)} to POPPYS_APP. No data was changed.')


if __name__ == '__main__':
    main()
