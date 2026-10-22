"""Let POPPYS AI add rows to (and, after approval, change rows of) one more existing GARMENT_DEMO table, so it can be
used as a linked table in the mapping (for example ORDFAB for fabrics).

Usage:  python scripts/allow_table.py ORDFAB
Grants SELECT, INSERT, UPDATE on that one table to POPPYS_APP. Never creates, changes or deletes tables or rows.
Tables created from the Database screen get these rights automatically."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import CONFIG, sqlplus  # noqa: E402


def main():
    if not CONFIG.exists():
        raise SystemExit('Run scripts/bootstrap_local.py first.')
    if len(sys.argv) != 2 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_$#]{0,127}', sys.argv[1]):
        raise SystemExit('Usage: python scripts/allow_table.py TABLE_NAME')
    table = sys.argv[1].upper()
    if table.startswith('PP_'):
        raise SystemExit('POPPYS audit tables cannot be used as order tables.')
    if sqlplus(f"select count(*) from dba_tables where owner='GARMENT_DEMO' and table_name='{table}';").strip() == '0':
        raise SystemExit(f'GARMENT_DEMO.{table} does not exist. Nothing was changed.')
    sqlplus(f'grant select, insert, update on GARMENT_DEMO.{table} to POPPYS_APP;')
    print(f'POPPYS AI can now add rows to GARMENT_DEMO.{table} (after your approval on each upload). No table or row was changed.')


if __name__ == '__main__':
    main()
