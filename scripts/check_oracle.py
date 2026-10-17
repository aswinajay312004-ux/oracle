"""Connection self-test using the application's own restricted account (.env.poppys).

    python scripts/check_oracle.py

Reports what POPPYS AI can see and write. Read-only.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from backend import oracle_store as oracle
    from backend.erp import mapping as registry
    from backend.erp.catalog import OracleCatalog
    try:
        conn = oracle.connect()
    except Exception as exc:
        raise SystemExit(f'FAILED to connect: {exc}\nCheck Docker Desktop, the oracle26ai container and .env.poppys.')
    with conn:
        print(f'Connected: Oracle {conn.version} at {conn.dsn} as {conn.username}')
        m = registry.effective()
        cat = OracleCatalog(conn); tables = registry.referenced_tables(m)
        visible = cat.columns(tables); insertable = cat.insertable(tables)
        for t in sorted(tables):
            print(f'  {t:10} visible={"yes" if t in visible else "NO ":3}  insert={"yes" if t in insertable else "no"}')
        c = conn.cursor()
        for obj in ('PP_IMPORT_LOG', 'PP_ORDER_KEYS', 'PP_WRITE_LOCK'):
            c.execute(f'select count(*) from {obj}')
            print(f'  {obj}: {c.fetchone()[0]} rows')
    try:
        ro = oracle.connect_readonly()
    except Exception as exc:
        print(f'Read-only account for Ask AI: NOT READY - {exc}')
    else:
        with ro:
            c = ro.cursor(); c.execute("select count(*) from all_tables where owner = 'GARMENT_DEMO'")
            print(f'Read-only account for Ask AI: {ro.username}, no write privileges, sees {c.fetchone()[0]} GARMENT_DEMO tables')
    print('All checks were read-only.')


if __name__ == '__main__':
    main()
