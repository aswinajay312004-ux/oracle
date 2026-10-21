"""Create the ONE order table POPPYS AI writes to: GARMENT_DEMO.EXCEL_ORDERS (one row per size).

Additive and safe to run again: it only creates the table when it is missing, and never drops or changes
any existing table or row. Run after scripts/bootstrap_local.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from bootstrap_local import CONFIG, sqlplus  # noqa: E402


def main():
    if not CONFIG.exists():
        raise SystemExit('Run scripts/bootstrap_local.py first.')
    from backend import order_table as t
    from backend.config import OWNER
    if sqlplus(f"select count(*) from dba_tables where owner='{OWNER}' and table_name='{t.TABLE}';").strip() != '0':
        print(f'{OWNER}.{t.TABLE} already exists. Nothing was changed.')
    else:
        sqlplus(';\n'.join(t.ddl()) + ';')
        print(f'Created {OWNER}.{t.TABLE} ({len(t.SPEC)} columns, one row per size, Excel labels stored as column comments).')
    sqlplus(f'grant select, insert, update on {OWNER}.{t.TABLE} to POPPYS_APP;')
    print('POPPYS AI can now read, save and (after approval) update orders in this table. No other table was touched.')


if __name__ == '__main__':
    main()
