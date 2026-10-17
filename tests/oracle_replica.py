"""Build a disposable GARMENT_DEMO *replica* inside an EMPTY test Oracle, so every Oracle path
(save, read-back, Ask AI, explorer, discovery) can be rehearsed on a real Oracle engine.

    docker run -d --name poppys-replica -p 1521:1521 -e ORACLE_PASSWORD=... \
        -e APP_USER=GARMENT_DEMO -e APP_USER_PASSWORD=... gvenzl/oracle-free:23-slim-faststart
    POPPYS_REPLICA_DSN=127.0.0.1:1521/FREEPDB1 POPPYS_REPLICA_PASSWORD=... python tests/oracle_replica.py [--orders 300000]

Safety: it connects only to the DSN given in POPPYS_REPLICA_DSN (never the app's configuration),
refuses to run if the GARMENT_DEMO schema already holds ANY table (so a real Axpert copy can never
be touched), and never drops, updates or deletes anything.

Tables and columns come from docs/live_schema_columns.json (the real column types and NOT NULL
rules). Primary keys are added on the ID columns and indexes on the order links. --orders N adds N
synthetic historic orders (2 lines x 4 sizes each) to rehearse large-table behaviour.

Large-database rehearsal (docs/PERFORMANCE.md): a SECOND disposable container on another port, e.g.
    docker run -d --name poppys-replica-big -p 1522:1521 ... gvenzl/oracle-free:23-slim-faststart
    POPPYS_REPLICA_DSN=127.0.0.1:1522/FREEPDB1 ... python tests/oracle_replica.py --orders 3000000   (24M size rows)
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_db  # noqa: E402

LINK_INDEXES = [('ORDSTYLE', 'ORDMASID'), ('ORDSIZE', 'ORDMASID, SUB3_SUBNO'), ('ORDFAB', 'ORDMASID'), ('ORDYARN', 'ORDMASID'),
                ('ORDACC', 'ORDMASID'), ('ORDCOMP', 'ORDMASID'), ('ORDSHIP', 'ORDMASID'), ('ORDCMTFAB', 'ORDMASID'), ('ORDDOC', 'ORDMASID')]


def coltype(c):
    if c['type'] == 'NUMBER':
        if c['precision'] is None and c['scale'] is None:
            return 'NUMBER'
        return f'NUMBER({c["precision"] or "*"},{c["scale"] or 0})'
    if c['type'] in ('VARCHAR2', 'CHAR'):
        return f'{c["type"]}({c["length"]} BYTE)'
    return c['type']


CHUNK = 250_000   # orders generated per INSERT: CONNECT BY holds every level in memory, so big runs go in chunks


def bulk(cur, n, chunk=CHUNK):
    """Server-side generation of n historic orders: n ORDMAS, 2n ORDSTYLE, 8n ORDSIZE rows, in chunks so any
    size loads (measured: 300k orders ≈ 35 s, 3M orders ≈ 6 min incl. statistics; see docs/PERFORMANCE.md)."""
    base = 10_000_000
    orders = 'SELECT LEVEL + :s l FROM dual CONNECT BY LEVEL <= :k'
    for start in range(0, n, chunk):
        k = min(chunk, n - start); t = time.time()
        cur.execute(f'''INSERT /*+ APPEND */ INTO ORDMAS (ORDMASID, DOCID, BUYER, STYLENO, STYLEDESC, ODATE, OQTY, BONO, UOM, MERCH, SAMPLETYPE, ORDCATEGORY, CANCEL)
            SELECT {base} + o.l, 'HX/' || LPAD(o.l, 7, '0'), MOD(o.l, 3) + 1, 11 + MOD(o.l, 3), 21 + MOD(o.l, 2),
                   DATE '2019-01-01' + MOD(o.l * 7, 2800), 400, 'PO-H' || o.l, 51, 61, 'BULK ORDER', 'PRODUCTION ORDER',
                   CASE WHEN MOD(o.l, 50) = 0 THEN 'T' ELSE 'F' END
              FROM ({orders}) o''', s=start, k=k)
        cur.connection.commit()
        cur.execute(f'''INSERT /*+ APPEND */ INTO ORDSTYLE (ORDSTYLEID, ORDMASID, SNO, SUBNO, COMBO, ORDQTY, SHIPDT, PONUM)
            SELECT ({base} + o.l) * 10 + s.n, {base} + o.l, s.n, ({base} + o.l) || ':' || s.n, 41 + MOD(o.l + s.n, 3), 200,
                   DATE '2019-03-01' + MOD(o.l * 7, 2800), 'PO-H' || o.l
              FROM ({orders}) o CROSS JOIN (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 2) s''', s=start, k=k)
        cur.connection.commit()
        cur.execute(f'''INSERT /*+ APPEND */ INTO ORDSIZE (ORDSIZEID, ORDMASID, SUB3_SUBNO, GSIZE, RQTY, PQTY, ORDSIZEROW)
            SELECT (({base} + o.l) * 10 + s.n) * 10 + z.r, {base} + o.l, ({base} + o.l) || ':' || s.n, 30 + z.r + MOD(o.l, 2), 50, 50, z.r
              FROM ({orders}) o
              CROSS JOIN (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 2) s
              CROSS JOIN (SELECT LEVEL r FROM dual CONNECT BY LEVEL <= 4) z''', s=start, k=k)
        cur.connection.commit()
        print(f'  {start + k:,} / {n:,} historic orders ({time.time() - t:.1f} s)', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--orders', type=int, default=0, help='extra synthetic historic orders (large-table rehearsal)')
    args = ap.parse_args()
    dsn = os.environ.get('POPPYS_REPLICA_DSN'); password = os.environ.get('POPPYS_REPLICA_PASSWORD')
    if not dsn or not password:
        raise SystemExit('Set POPPYS_REPLICA_DSN and POPPYS_REPLICA_PASSWORD (the TEST container, never your real database).')
    import oracledb
    with oracledb.connect(user='GARMENT_DEMO', password=password, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM user_tables')
        if cur.fetchone()[0]:
            raise SystemExit('Refused: GARMENT_DEMO already has tables. The replica is only built in an empty test schema.')
        tables = demo_db.columns()
        for t, cols in tables.items():
            body = ', '.join(f'{c["column"]} {coltype(c)}{" NOT NULL" if c["nullable"] == "N" else ""}' for c in cols)
            key = f'{t}ID' if any(c['column'] == f'{t}ID' for c in cols) else None
            cur.execute(f'CREATE TABLE {t} ({body}{f", CONSTRAINT {t}_PK PRIMARY KEY ({key})" if key else ""})')
        for t, cols in LINK_INDEXES:
            if t in tables:
                cur.execute(f'CREATE INDEX {t}_ORD_IX ON {t} ({cols})')
        cur.execute('CREATE INDEX ORDMAS_DOCID_IX ON ORDMAS (DOCID)')
        by_table = {}
        for t, v in demo_db.rows():
            by_table.setdefault((t, tuple(v)), []).append(list(v.values()))
        for (t, names), data in by_table.items():
            cur.executemany(f'INSERT INTO {t} ({",".join(names)}) VALUES ({",".join(":" + str(i + 1) for i in range(len(names)))})', data)
        conn.commit()
        if args.orders:
            bulk(cur, args.orders)
        t = time.time()
        cur.callproc('DBMS_STATS.GATHER_SCHEMA_STATS', ['GARMENT_DEMO'])
        print(f'  statistics gathered ({time.time() - t:.1f} s)', flush=True)
        cur.execute('SELECT table_name, num_rows FROM user_tables ORDER BY table_name')
        print('Replica ready:', ', '.join(f'{t}={n}' for t, n in cur))


if __name__ == '__main__':
    main()
