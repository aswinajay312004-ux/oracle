"""Synthetic GARMENT_DEMO data shared by two test databases:

* create()            - an in-memory SQLite copy of the *structure* (from the committed dictionary
                        snapshot), used by the fast cloud tests; dates are ISO text there.
* rows()              - the same records with real Python types, loaded into a disposable Oracle
                        replica by tests/oracle_replica.py so the Oracle paths run on a real engine.

Every value respects the real column types of docs/live_schema_columns.json (for example
BUYMAS.COUNTRY is a NUMBER code in Axpert, so no text is stored there)."""
from __future__ import annotations
from datetime import date
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def columns():
    tables = {}
    for c in json.loads((ROOT / 'docs' / 'live_schema_columns.json').read_text(encoding='utf-8')):
        tables.setdefault(c['table'], []).append(c)
    return tables


def rows():
    out = []

    def ins(table, **values):
        out.append((table, values))

    for i, name in enumerate(['NEXT', 'ZARA', 'H&M KIDS'], 1):
        ins('BUYMAS', BUYMASID=i, BUYERNAME=name, BUYERCODE=name[:3], CANCEL='F')
    ins('BUYMAS', BUYMASID=9, BUYERNAME='OLD BUYER', CANCEL='T')
    for i, s in enumerate(['N44441', 'Z100', 'H200'], 11):
        ins('STYLENUM', STYLENUMID=i, STYLENUMBER=s)
    ins('STYLEMAS', STYLEMASID=21, SDESC='GIRLS PRINTED T-SHIRT')
    ins('STYLEMAS', STYLEMASID=22, SDESC='BOYS SHORTS')
    for i, s in enumerate(['3-6M', '6-9M', '9-12M', 'S', 'M'], 31):
        ins('SIZE_MAS', SIZE_MASID=i, SIZES=s)
    for i, c in enumerate(['WHITE AOP', 'PINK SOLID', 'NAVY'], 41):
        ins('COLORMAS', COLORMASID=i, COLOR=c)
    ins('UNITMAS', UNITMASID=51, UOM='PCS')
    ins('UNITMAS', UNITMASID=52, UOM='KGS')
    ins('MERCHMAS', MERCHMASID=61, NAME='PRIYA')
    orders = [  # id, ref, buyer, style, desc, date, lines [(color, {size: qty})]
        (101, 'PK/26-27/S/9001', 1, 11, 21, date(2026, 10, 5), [(41, {31: 100, 32: 150, 33: 50}), (42, {31: 80, 32: 70})]),
        (102, 'PK/26-27/S/9002', 1, 11, 21, date(2026, 9, 12), [(43, {34: 200, 35: 300})]),
        (103, 'PK/26-27/S/9003', 2, 12, 22, date(2026, 10, 10), [(41, {34: 40, 35: 60})]),
        (104, 'PK/26-27/S/9004', 9, 13, 22, date(2026, 10, 11), [(42, {34: 1})]),
    ]
    sid = 1000
    for oid, ref, buyer, style, desc, odate, lines in orders:
        total = sum(sum(s.values()) for _, s in lines)
        ins('ORDMAS', ORDMASID=oid, DOCID=ref, BUYER=buyer, STYLENO=style, STYLEDESC=desc, ODATE=odate, OQTY=total, BONO=f'PO-{oid}',
            UOM=51, MERCH=61, SAMPLETYPE='DEVELOPMENT SAMPLE', ORDCATEGORY='SAMPLE ORDER', CANCEL='T' if oid == 104 else 'F')
        for n, (color, sz) in enumerate(lines, 1):
            sub = f'{oid}:{n}'
            ins('ORDSTYLE', ORDSTYLEID=oid * 10 + n, ORDMASID=oid, SNO=n, SUBNO=sub, COMBO=color, ORDQTY=sum(sz.values()),
                SHIPDT=date(2026, 12, 1), PONUM=f'PO-{oid}')
            for r, (size, q) in enumerate(sz.items(), 1):
                sid += 1
                ins('ORDSIZE', ORDSIZEID=sid, ORDMASID=oid, SUB3_SUBNO=sub, GSIZE=size, RQTY=q, PQTY=q, ORDSIZEROW=r)
            ins('ORDFAB', ORDFABID=oid * 100 + n, ORDMASID=oid, FSUBNO=sub, COLOUR=color, FGSM='180', DIA='30', CADWT=125,
                POQTY=round(sum(sz.values()) * 125 / 1000 * 1.03, 3), YDET=f'{sub}:Y')
            ins('ORDYARN', ORDYARNID=oid * 1000 + n, ORDMASID=oid, SUB9_YDET=f'{sub}:Y', YCOLOR=color, YPER=100,
                YQTY=round(sum(sz.values()) * 125 / 1000 * 1.03, 3))
    return out


def create():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    for t, cols in columns().items():
        conn.execute(f'CREATE TABLE {t} ({", ".join(c["column"] for c in cols)})')
    for table, values in rows():
        vals = [v.isoformat() if isinstance(v, date) else v for v in values.values()]
        conn.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" * len(values))})', vals)
    conn.commit()
    return conn
