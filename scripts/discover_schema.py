"""READ-ONLY discovery of the local GARMENT_DEMO schema. Run on the Windows laptop:

    python scripts/discover_schema.py

Connects as GARMENT_DEMO (password asked with getpass, or POPPYS_DISCOVERY_PASSWORD), runs only
SELECT statements against the data dictionary and the order tables, and writes the evidence to
data/schema_discovery.json + data/schema_discovery.md (git-ignored, may contain sample values).
The Database mapping screen shows this evidence next to every proposed column.
"""
from __future__ import annotations
from datetime import datetime
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'data'
OWNER = 'GARMENT_DEMO'
FOCUS = ('ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'ORDFAB', 'ORDYARN', 'ORDACC', 'ORDCOMP', 'ORDSHIP', 'ORDCMTFAB', 'ORDERENTRY', 'ORDDOC')
# (child table, child column, parent table, parent column) — tested within the same ORDMASID.
LINKS = [
    ('ORDSIZE', 'SUB3_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDFAB', 'FSUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDYARN', 'SUB9_FSUBNO', 'ORDFAB', 'FSUBNO'),
    ('ORDYARN', 'SUB9_YDET', 'ORDFAB', 'YDET'),
    ('ORDACC', 'ASUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDCOMP', 'SUB5_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDSHIP', 'SUB6_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDCMTFAB', 'SUB8_SUBNO', 'ORDSTYLE', 'SUBNO'),
]
# Numeric reference columns: which master does the value resolve in? (candidate, key)
REFERENCES = {
    'ORDMAS.BUYER': ('BUYMAS', 'BUYMASID'), 'ORDMAS.STYLENO': ('STYLENUM', 'STYLENUMID'), 'ORDMAS.STYLEDESC': ('STYLEMAS', 'STYLEMASID'),
    'ORDMAS.UOM': ('UNITMAS', 'UNITMASID'), 'ORDMAS.MERCH': ('MERCHMAS', 'MERCHMASID'), 'ORDMAS.ENAME': ('PARTYMAS', 'PARTYMASID'),
    'ORDSTYLE.COMBO': ('COLORMAS', 'COLORMASID'), 'ORDSIZE.GSIZE': ('SIZE_MAS', 'SIZE_MASID'),
    'ORDFAB.COLOUR': ('COLORMAS', 'COLORMASID'), 'ORDFAB.FUOM': ('UNITMAS', 'UNITMASID'), 'ORDYARN.YCOLOR': ('COLORMAS', 'COLORMASID'),
    'ORDACC.ACCCOLOR': ('COLORMAS', 'COLORMASID'), 'ORDACC.AUOM': ('UNITMAS', 'UNITMASID'), 'ORDACC.ACCSIZES': ('SIZE_MAS', 'SIZE_MASID'),
}


def connect():
    import oracledb
    from dotenv import dotenv_values
    cfg = dotenv_values(ROOT / '.env.poppys')
    dsn = os.environ.get('POPPYS_ORACLE_DSN') or cfg.get('ORACLE_DSN') or '127.0.0.1:1521/FREEPDB1'
    password = os.environ.get('POPPYS_DISCOVERY_PASSWORD') or getpass.getpass(f'{OWNER} password for {dsn}: ')
    return oracledb.connect(user=OWNER, password=password, dsn=dsn, tcp_connect_timeout=6)


def rows(c, sql, **binds):
    c.execute(sql, binds)
    names = [d[0].lower() for d in c.description]
    return [dict(zip(names, r)) for r in c]


def text(v):
    if v is None:
        return None
    if hasattr(v, 'read'):
        v = v.read()
    if isinstance(v, datetime):
        return v.isoformat()[:19]
    return v if isinstance(v, (int, float)) else str(v)[:120]


def main():
    conn = connect(); c = conn.cursor()
    tables = rows(c, 'select table_name, num_rows from all_tables where owner=:o order by table_name', o=OWNER)
    columns = rows(c, '''select table_name, column_name, data_type, data_length, data_precision, data_scale, nullable, column_id
        from all_tab_columns where owner=:o order by table_name, column_id''', o=OWNER)
    constraints = rows(c, '''select c.table_name, c.constraint_name, c.constraint_type, c.status, c.search_condition_vc,
            r.table_name ref_table, (select listagg(cc.column_name, ',') within group (order by cc.position) from all_cons_columns cc
              where cc.owner=c.owner and cc.constraint_name=c.constraint_name) cols,
            (select listagg(rc.column_name, ',') within group (order by rc.position) from all_cons_columns rc
              where rc.owner=c.r_owner and rc.constraint_name=c.r_constraint_name) ref_cols
        from all_constraints c left join all_constraints r on r.owner=c.r_owner and r.constraint_name=c.r_constraint_name
        where c.owner=:o and c.constraint_type in ('P','U','R','C')''', o=OWNER)
    # NOT NULL is already visible as column nullability; keep business CHECK constraints only.
    constraints = [x for x in constraints if not (x['constraint_type'] == 'C' and str(x['search_condition_vc'] or '').upper().endswith('IS NOT NULL'))]
    triggers = rows(c, '''select trigger_name, table_name, triggering_event, trigger_type, status, substr(description,1,400) description
        from all_triggers where owner=:o order by table_name''', o=OWNER)
    for t in triggers:
        body = rows(c, 'select trigger_body from all_triggers where owner=:o and trigger_name=:n', o=OWNER, n=t['trigger_name'])
        t['body'] = text(body[0]['trigger_body'])[:2000] if body and body[0]['trigger_body'] is not None else None
    sequences = rows(c, 'select sequence_name, last_number, increment_by from all_sequences where sequence_owner=:o', o=OWNER)
    objects = rows(c, '''select object_name, object_type, status from all_objects where owner=:o
        and object_type in ('PACKAGE','PROCEDURE','FUNCTION','VIEW','TRIGGER') order by object_type, object_name''', o=OWNER)

    evidence = {}
    by_table = {}
    for col in columns:
        by_table.setdefault(col['table_name'], []).append(col)
    for table in FOCUS:
        if table not in by_table:
            continue
        c.execute(f'select count(*) from {OWNER}.{table}'); total = c.fetchone()[0]
        for col in by_table[table]:
            name = col['column_name']
            if col['data_type'] in ('BLOB', 'CLOB', 'LONG', 'RAW', 'LONG RAW'):
                continue
            c.execute(f'select count({name}), count(distinct {name}) from {OWNER}.{table}')
            filled, distinct = c.fetchone()
            c.execute(f'select {name}, count(*) n from {OWNER}.{table} where {name} is not null group by {name} order by n desc fetch first 5 rows only')
            samples = [text(r[0]) for r in c]
            evidence[f'{table}.{name}'] = dict(rows=total, filled=filled, fill_pct=round(100 * filled / total, 1) if total else None,
                                               distinct=distinct, samples=samples)
    for key, (master, mkey) in REFERENCES.items():
        table, col = key.split('.')
        if table in by_table and master in by_table and key in evidence:
            c.execute(f'''select count(*), sum(case when exists (select 1 from {OWNER}.{master} m where m.{mkey}=t.{col}) then 1 else 0 end)
                from {OWNER}.{table} t where t.{col} is not null''')
            n, ok = c.fetchone()
            evidence[key]['resolves_in'] = dict(table=master, key=mkey, rows=n, matched=ok or 0,
                                                match_pct=round(100 * (ok or 0) / n, 1) if n else None)
    links = []
    for child, ccol, parent, pcol in LINKS:
        if child in by_table and parent in by_table and all(x in [k['column_name'] for k in by_table[t]] for t, x in ((child, ccol), (parent, pcol))):
            c.execute(f'''select count(*), sum(case when exists (select 1 from {OWNER}.{parent} p where p.ORDMASID=t.ORDMASID and p.{pcol}=t.{ccol}) then 1 else 0 end)
                from {OWNER}.{child} t where t.{ccol} is not null''')
            n, ok = c.fetchone()
            link = dict(child=f'{child}.{ccol}', parent=f'{parent}.{pcol}', rows=n, matched=ok or 0, match_pct=round(100 * (ok or 0) / n, 1) if n else None)
            links.append(link)
            evidence.setdefault(f'{child}.{ccol}', {})['link'] = link
    for con in constraints:
        if con['constraint_type'] == 'R':
            for col, rc in zip((con['cols'] or '').split(','), (con['ref_cols'] or '').split(',')):
                evidence.setdefault(f'{con["table_name"]}.{col}', {})['foreign_key'] = f'{con["ref_table"]}.{rc}'

    result = dict(generated_at=datetime.now().astimezone().isoformat(timespec='seconds'), owner=OWNER, dsn=conn.dsn,
                  database_version=conn.version, tables=tables, dictionary=columns, constraints=constraints, triggers=triggers,
                  sequences=sequences, objects=objects, links=links, columns=evidence)
    OUT.mkdir(exist_ok=True)
    (OUT / 'schema_discovery.json').write_text(json.dumps(result, indent=1, default=str, ensure_ascii=False), encoding='utf-8')

    md = [f'# GARMENT_DEMO discovery — {result["generated_at"]}', '', f'Oracle {conn.version}, {len(tables)} tables, '
          f'{len(columns)} columns, {len(triggers)} triggers, {len(objects)} code objects.', '', '## Tables', '']
    md += [f'- {t["table_name"]} ({t["num_rows"] if t["num_rows"] is not None else "?"} rows by statistics)' for t in tables]
    md += ['', '## Declared constraints', ''] + [f'- {x["table_name"]}: {x["constraint_type"]} {x["cols"]}'
                                                  + (f' → {x["ref_table"]}({x["ref_cols"]})' if x['ref_table'] else '') for x in constraints]
    md += ['', '## Triggers (save-time behaviour)', ''] + [f'- {t["table_name"]}.{t["trigger_name"]}: {t["triggering_event"]} {t["status"]}' for t in triggers]
    md += ['', '## Child → parent link evidence (same ORDMASID)', ''] + [
        f'- {l["child"]} → {l["parent"]}: {l["matched"]}/{l["rows"]} rows match ({l["match_pct"]}%)' for l in links]
    md += ['', '## Reference columns resolving in masters', ''] + [
        f'- {k}: {v["resolves_in"]["matched"]}/{v["resolves_in"]["rows"]} in {v["resolves_in"]["table"]}' for k, v in evidence.items() if 'resolves_in' in v]
    (OUT / 'schema_discovery.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md))
    print(f'\nSaved {OUT / "schema_discovery.json"} (git-ignored). Open POPPYS AI → Database mapping to review the evidence.')
    print('No data was changed: this script only runs SELECT statements.')


if __name__ == '__main__':
    main()
