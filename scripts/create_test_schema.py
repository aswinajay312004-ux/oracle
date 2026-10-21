"""Create a SYNTHETIC GARMENT_DEMO schema for a fresh development/CI Oracle container.

Uses only the column metadata snapshot in docs/live_schema_columns.json and a few
labelled TEST master rows. It refuses to run when GARMENT_DEMO already exists, so it
can never modify the client's real demo schema. Run scripts/bootstrap_local.py and
scripts/upgrade_local.py afterwards, exactly as on the client machine.
"""
from collections import defaultdict
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_local import ROOT, sqlplus  # noqa: E402

MASTERS = {
    'BUYMAS': ('BUYMASID', 'BUYERNAME', ['TEST BUYER TEXTILES', 'ABC TEXTILES']),
    'STYLENUM': ('STYLENUMID', 'STYLENUMBER', ['TEST-STYLE-001']),
    'STYLEMAS': ('STYLEMASID', 'SDESC', ['TEST BODYSUIT']),
    'SIZE_MAS': ('SIZE_MASID', 'SIZES', ['S', 'M', 'L', 'XL']),
    'COLORMAS': ('COLORMASID', 'COLOR', ['NAVY', 'WHITE']),
    'UNITMAS': ('UNITMASID', 'UOM', ['PCS']),
    'MERCHMAS': ('MERCHMASID', 'NAME', ['TEST MERCHANT']),
}


def column_type(c):
    if c['type'] == 'NUMBER':
        if c['precision']:
            return f"NUMBER({c['precision']},{c['scale'] or 0})"
        return 'NUMBER' if c['scale'] is None else f"NUMBER(38,{c['scale']})"
    if c['type'] in ('VARCHAR2', 'CHAR'):
        return f"{c['type']}({c['length']} BYTE)"
    return c['type']


def main():
    if sqlplus("select count(*) from dba_users where username='GARMENT_DEMO';").strip() != '0':
        raise SystemExit('GARMENT_DEMO already exists. This script only creates a synthetic test schema.')
    columns = json.loads((ROOT / 'docs' / 'live_schema_columns.json').read_text(encoding='utf-8'))
    tables = defaultdict(list)
    for c in columns:
        tables[c['table']].append(c)
    ddl = [f'create user GARMENT_DEMO identified by "Gd{secrets.token_hex(12)}" default tablespace USERS quota unlimited on USERS;',
           'grant create session, create table, create sequence to GARMENT_DEMO;']
    for table, cols in tables.items():
        body = ',\n '.join(f'"{c["column"]}" {column_type(c)}{" NOT NULL" if c["nullable"] == "N" else ""}' for c in cols)
        pk = next((c['column'] for c in cols if c['nullable'] == 'N'), None)
        constraint = f',\n CONSTRAINT "PK_{table}" PRIMARY KEY ("{pk}")' if pk else ''
        ddl.append(f'create table GARMENT_DEMO."{table}" (\n {body}{constraint});')
    for table, (pk, label, values) in MASTERS.items():
        for i, value in enumerate(values, 1):
            ddl.append(f"insert into GARMENT_DEMO.\"{table}\" (\"{pk}\",\"{label}\",\"CANCEL\") values ({i},'{value}','F');")
    ddl.append('commit;')
    sqlplus('\n'.join(ddl))
    print(f'Created synthetic GARMENT_DEMO with {len(tables)} tables and TEST master rows.')


if __name__ == '__main__':
    main()
